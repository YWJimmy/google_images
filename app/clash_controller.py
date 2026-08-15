from __future__ import annotations

import ipaddress
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


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

    def _request(self, path: str, method: str = "GET", payload: dict | None = None) -> dict:
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
            with urlopen(request, timeout=3) as response:
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
