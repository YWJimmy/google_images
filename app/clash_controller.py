from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import ipaddress
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
GROUP_PROXY_TYPES = {"Selector", "URLTest", "Fallback", "LoadBalance", "Relay"}
NON_TESTABLE_PROXY_TYPES = {"Direct", "Reject", "RejectDrop", "Pass", "Compatible"}
DEFAULT_DELAY_TEST_URL = "https://cp.cloudflare.com/generate_204"


class ClashControllerError(RuntimeError):
    pass


def validate_local_http_url(value: str, name: str) -> str:
    url = value.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS or not parts.port:
        raise ValueError(f"{name} must be a local http URL with an explicit port")
    return url


def validate_secret(value: str) -> str:
    secret = value.strip()
    if not secret or len(secret) > 512 or any(ord(char) < 32 for char in secret):
        raise ValueError("Clash API secret is required")
    return secret


def mask_ip(value: str) -> str:
    address = ipaddress.ip_address(value.strip())
    if address.version == 4:
        parts = str(address).split(".")
        return ".".join([*parts[:3], "xxx"])
    parts = address.exploded.split(":")
    return ":".join(parts[:4]) + ":…"


class ClashController:
    def __init__(self, endpoint: str, secret: str):
        self.endpoint = validate_local_http_url(endpoint, "controller endpoint")
        self.secret = validate_secret(secret)

    def _request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        request_timeout: float = 3,
    ) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.endpoint + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.secret}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=request_timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raise ClashControllerError(f"Clash controller returned HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise ClashControllerError("Clash controller connection failed") from None

    def status(self) -> dict:
        version = self._request("/version")
        selectors = self.selectors()
        return {
            "online": True,
            "version": str(version.get("version", "unknown"))[:80],
            "selectors": selectors,
        }

    def selectors(self) -> list[dict]:
        state = self._request("/proxies")
        proxies = state.get("proxies", {})
        if not isinstance(proxies, dict):
            raise ClashControllerError("Clash controller returned an invalid proxy list")
        result = []
        for group_name, value in proxies.items():
            if not isinstance(value, dict) or value.get("type") != "Selector":
                continue
            choices = [str(item) for item in value.get("all", []) if isinstance(item, str)]
            result.append(
                {
                    "group": str(group_name),
                    "current": str(value.get("now", "")),
                    "choices": choices,
                }
            )
        return result

    def switch(self, group: str, node: str) -> dict:
        group_name = group.strip()
        node_name = node.strip()
        selectors = {item["group"]: item for item in self.selectors()}
        if group_name not in selectors:
            raise ValueError("unknown Selector group")
        if node_name not in selectors[group_name]["choices"]:
            raise ValueError("node is not a current choice of this Selector group")
        self._request(f"/proxies/{quote(group_name, safe='')}", "PUT", {"name": node_name})
        return {"ok": True, "group": group_name, "current": node_name}

    def _proxy_state(self) -> dict[str, dict]:
        state = self._request("/proxies")
        proxies = state.get("proxies", {})
        if not isinstance(proxies, dict):
            raise ClashControllerError("Clash controller returned an invalid proxy list")
        return {str(name): value for name, value in proxies.items() if isinstance(value, dict)}

    @staticmethod
    def _leaf_nodes(proxies: dict[str, dict], group: str) -> list[str]:
        if group not in proxies or str(proxies[group].get("type", "")) != "Selector":
            raise ValueError("unknown Selector group")
        leaves: list[str] = []
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            value = proxies.get(name, {})
            proxy_type = str(value.get("type", ""))
            if proxy_type in GROUP_PROXY_TYPES:
                for child in value.get("all", []):
                    if isinstance(child, str):
                        visit(child)
                return
            if proxy_type and proxy_type not in NON_TESTABLE_PROXY_TYPES:
                leaves.append(name)

        for choice in proxies[group].get("all", []):
            if isinstance(choice, str):
                visit(choice)
        return leaves

    def _delay_for_node(self, name: str, timeout_ms: int) -> int:
        query = urlencode(
            {"url": DEFAULT_DELAY_TEST_URL, "timeout": timeout_ms, "expected": "204"}
        )
        response = self._request(
            f"/proxies/{quote(name, safe='')}/delay?{query}",
            request_timeout=(timeout_ms / 1000) + 2,
        )
        delay = response.get("delay")
        if not isinstance(delay, int) or delay <= 0 or delay >= 65535:
            raise ClashControllerError("proxy delay check failed")
        return delay

    def probe_group_nodes(
        self, group: str, *, timeout_ms: int = 3000, max_nodes: int = 200
    ) -> dict:
        """Test a Selector's leaf nodes without changing the selected node."""
        if not 500 <= timeout_ms <= 10000:
            raise ValueError("timeout_ms must be between 500 and 10000")
        if not 1 <= max_nodes <= 500:
            raise ValueError("max_nodes must be between 1 and 500")
        proxies = self._proxy_state()
        names = self._leaf_nodes(proxies, group)
        truncated = len(names) > max_nodes
        names = names[:max_nodes]
        results: dict[str, dict] = {}
        workers = min(8, max(1, len(names)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._delay_for_node, name, timeout_ms): name for name in names}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    delay = future.result()
                    results[name] = {
                        "name": name,
                        "type": str(proxies[name].get("type", "unknown"))[:40],
                        "usable": True,
                        "delay_ms": delay,
                        "status": "可用",
                    }
                except Exception:
                    results[name] = {
                        "name": name,
                        "type": str(proxies[name].get("type", "unknown"))[:40],
                        "usable": False,
                        "delay_ms": None,
                        "status": "超时或不可用",
                    }
        ordered = sorted(
            results.values(),
            key=lambda item: (not item["usable"], item["delay_ms"] or 10**9, item["name"]),
        )
        return {
            "group": group,
            "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "test_method": "mihomo_http_delay",
            "test_url": DEFAULT_DELAY_TEST_URL,
            "timeout_ms": timeout_ms,
            "tested": len(ordered),
            "usable": sum(1 for item in ordered if item["usable"]),
            "unusable": sum(1 for item in ordered if not item["usable"]),
            "truncated": truncated,
            "nodes": ordered,
        }


def masked_proxy_egress(proxy_url: str) -> dict:
    proxy = validate_local_http_url(proxy_url, "proxy URL")
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
    request = Request("https://api.ipify.org", headers={"User-Agent": "google-images-local-check/1"})
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=6) as response:
            raw = response.read().decode("ascii").strip()
        address = ipaddress.ip_address(raw)
        return {
            "ok": True,
            "family": f"IPv{address.version}",
            "masked_ip": mask_ip(raw),
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise ClashControllerError("proxy egress check failed") from None
