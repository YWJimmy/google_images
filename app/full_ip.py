"""Collect and validate the real public IP seen through a local proxy."""

from __future__ import annotations

from collections import Counter
import ipaddress
import json
import time
from typing import Any, Callable
from urllib.request import ProxyHandler, Request, build_opener

from .clash_controller import validate_local_http_url


PROVIDERS = (
    ("ipify", "https://api.ipify.org?format=json"),
    ("ifconfig.me", "https://ifconfig.me/ip"),
    ("ipinfo", "https://ipinfo.io/json"),
)


def _default_fetch(url: str, proxy_url: str, timeout: float) -> str:
    proxy = validate_local_http_url(proxy_url, "proxy URL")
    opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
    request = Request(url, headers={"User-Agent": "ip-intelligence/1.0"})
    with opener.open(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="strict").strip()


def _parse_response(provider: str, payload: str) -> tuple[str, str | None]:
    country = None
    if provider in {"ipify", "ipinfo"}:
        value = json.loads(payload)
        raw_ip = value.get("ip")
        country = value.get("country") if provider == "ipinfo" else None
    else:
        raw_ip = payload
    address = ipaddress.ip_address(str(raw_ip).strip())
    return str(address), str(country).upper() if country else None


class FullIpCollector:
    def __init__(self, fetcher: Callable[[str, str, float], str] | None = None,
                 providers: tuple[tuple[str, str], ...] = PROVIDERS):
        self.fetcher = fetcher or _default_fetch
        self.providers = providers

    def collect(self, proxy_url: str, *, timeout: float = 6,
                minimum_consensus: int = 1) -> dict[str, Any]:
        started = time.perf_counter()
        observations: list[dict[str, Any]] = []
        for provider, url in self.providers:
            try:
                full_ip, country = _parse_response(
                    provider, self.fetcher(url, proxy_url, timeout)
                )
                observations.append({
                    "provider": provider, "full_ip": full_ip, "country": country
                })
            except Exception as exc:
                observations.append({
                    "provider": provider,
                    "error": type(exc).__name__,
                })

        valid = [item for item in observations if item.get("full_ip")]
        if not valid:
            return {
                "ok": False,
                "error_code": "FULL_IP_UNAVAILABLE",
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "observations": observations,
            }
        counts = Counter(str(item["full_ip"]) for item in valid)
        full_ip, votes = counts.most_common(1)[0]
        required = max(1, min(int(minimum_consensus), len(self.providers)))
        if votes < required:
            return {
                "ok": False,
                "error_code": "IP_PROVIDER_MISMATCH",
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "observations": observations,
            }
        matching = [item for item in valid if item["full_ip"] == full_ip]
        country = next((item.get("country") for item in matching if item.get("country")), None)
        return {
            "ok": True,
            "full_ip": full_ip,
            "family": f"IPv{ipaddress.ip_address(full_ip).version}",
            "country": country,
            "consensus": votes,
            "providers_ok": len(valid),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "observations": observations,
        }


def mask_full_ip(value: str | None) -> str | None:
    if not value:
        return None
    address = ipaddress.ip_address(value)
    if address.version == 4:
        parts = str(address).split(".")
        return ".".join(parts[:2] + ["xxx", "xxx"])
    parts = address.exploded.split(":")
    return ":".join(parts[:2] + ["xxxx"] * 6)

