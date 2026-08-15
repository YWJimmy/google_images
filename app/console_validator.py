from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
REQUIRED_EVENT_FIELDS = {
    "run_id",
    "verification_ordinal",
    "event_type",
    "task_index",
    "search_sequence_number",
    "task_attempt_number",
    "profile_name",
    "masked_ip",
    "proxy_status",
    "ip_event_status",
    "proxy_latency_ms",
}


def validate_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS or not parts.port:
        raise ValueError("dashboard URL must be a loopback http URL with an explicit port")
    return url


def html_contract(html: str) -> dict[str, object]:
    ids = re.findall(r'\bid="([^"]+)"', html)
    references = re.findall(r"\$\('([^']+)'\)", html)
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    missing = sorted(set(references) - set(ids))
    return {
        "id_count": len(ids),
        "reference_count": len(set(references)),
        "duplicate_ids": duplicates,
        "missing_references": missing,
    }


class ConsoleValidator:
    def __init__(self, base_url: str):
        self.base_url = validate_base_url(base_url)
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def _request(
        self, path: str, *, method: str = "GET", payload: dict | None = None
    ) -> tuple[int, bytes, dict]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urlopen(request, timeout=8) as response:
                return response.status, response.read(), dict(response.headers.items())
        except HTTPError as exc:
            return exc.code, exc.read(), dict(exc.headers.items())
        except (URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"dashboard request failed: {type(exc).__name__}") from None

    def _json(self, path: str, *, method: str = "GET", payload: dict | None = None):
        status, body, headers = self._request(path, method=method, payload=payload)
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            value = None
        return status, value, headers

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"[PASS] {name}", flush=True)
        else:
            self.failed += 1
            print(f"[FAIL] {name}{': ' + detail if detail else ''}", flush=True)

    def skip(self, name: str, detail: str) -> None:
        self.skipped += 1
        print(f"[SKIP] {name}: {detail}", flush=True)

    def run(self, *, skip_operation: bool = False) -> int:
        status, html_bytes, headers = self._request("/")
        html = html_bytes.decode("utf-8", errors="replace")
        self.check("dashboard page responds", status == 200, str(status))
        self.check("dashboard disables caching", headers.get("Cache-Control") == "no-store")
        csp = headers.get("Content-Security-Policy", "")
        self.check("dashboard CSP limits connections to self", "connect-src 'self'" in csp)
        contract = html_contract(html)
        self.check("HTML IDs are unique", not contract["duplicate_ids"], str(contract))
        self.check("JavaScript element references exist", not contract["missing_references"], str(contract))
        for label in ("阶段耗时", "最近人工验证记录", "代理延时", "操作中心"):
            self.check(f"page contains {label}", label in html)

        responses = {}
        for path in (
            "/api/status",
            "/api/profiles",
            "/api/profiles/suggest",
            "/api/operations",
            "/api/settings",
            "/api/verification-events?limit=50",
        ):
            code, value, _ = self._json(path)
            responses[path] = value
            self.check(f"GET {path}", code == 200 and isinstance(value, dict), str(code))

        settings = responses.get("/api/settings") or {}
        allowed_settings = {
            "clash_secret_saved",
            "clash_endpoint",
            "clash_proxy_url",
            "storage_path",
            "protection",
        }
        self.check("settings response has no secret value", set(settings) <= allowed_settings)
        profiles = (responses.get("/api/profiles") or {}).get("profiles", [])
        required_profile = {
            "name",
            "endpoint",
            "online",
            "proxy_status",
            "masked_ip",
            "proxy_latency_ms",
        }
        self.check(
            "profile response has network state",
            all(required_profile <= set(profile) for profile in profiles),
        )
        events = (responses.get("/api/verification-events?limit=50") or {}).get("events", [])
        self.check(
            "verification response has numbered IP snapshot fields",
            all(REQUIRED_EVENT_FIELDS <= set(event) for event in events),
        )

        invalid_requests = (
            ("/api/chromes", {"label": "invalid", "endpoint": "http://example.com:9222"}),
            ("/api/history", {"url": "javascript:alert(1)"}),
            (
                "/api/profiles/proxy",
                {"name": profiles[0]["name"] if profiles else "missing", "proxy_url": "http://203.0.113.1:7898"},
            ),
            (
                "/api/settings/clash/save",
                {
                    "secret": "",
                    "endpoint": "http://127.0.0.1:9097",
                    "proxy_url": "http://127.0.0.1:7897",
                },
            ),
        )
        for path, payload in invalid_requests:
            code, _value, _headers = self._json(path, method="POST", payload=payload)
            self.check(f"reject invalid {path}", code == 400, str(code))

        if skip_operation:
            self.skip("operation lifecycle", "self-check is already running inside operation center")
        else:
            operation = responses.get("/api/operations") or {}
            if operation.get("active"):
                self.skip("operation lifecycle", "another operation is active")
            else:
                code, _value, _headers = self._json(
                    "/api/operations/start",
                    method="POST",
                    payload={"action": "validate", "endpoint": "http://127.0.0.1:9222"},
                )
                self.check("start validation operation", code == 200, str(code))
                deadline = time.monotonic() + 15
                final = {}
                while time.monotonic() < deadline:
                    _code, final, _headers = self._json("/api/operations")
                    if not final.get("active"):
                        break
                    time.sleep(0.25)
                self.check(
                    "validation operation completes",
                    final.get("status") == "complete" and final.get("return_code") == 0,
                    str(final.get("status")),
                )
                self.check("validation operation streams output", bool(final.get("output")))
                self.check("validation operation records activity", bool(final.get("last_activity_at")))

        summary = {"passed": self.passed, "failed": self.failed, "skipped": self.skipped}
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0 if self.failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the local dashboard contract")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--skip-operation", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return ConsoleValidator(args.base_url).run(skip_operation=args.skip_operation)
    except (ValueError, RuntimeError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
