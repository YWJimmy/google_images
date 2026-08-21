"""Verification adapters for proxy and real Chrome egress."""

from __future__ import annotations

import ipaddress
import json
from typing import Any, Callable

from .full_ip import FullIpCollector


class FullIpVerifier:
    def __init__(self, collector: FullIpCollector | None = None):
        self.collector = collector or FullIpCollector()

    def snapshot(self, proxy_url: str) -> dict[str, Any]:
        return self.collector.collect(proxy_url)

    def verify_change(self, proxy_url: str, old_full_ip: str | None) -> dict[str, Any]:
        sample = self.snapshot(proxy_url)
        if not sample.get("ok"):
            return {**sample, "changed": False}
        return {**sample, "changed": sample.get("full_ip") != old_full_ip}


class ChromeIpVerifier:
    """Open a short-lived CDP tab so the sample uses Chrome's actual network path."""

    def __init__(self, checker: Callable[[str], str] | None = None):
        self.checker = checker or self._playwright_check

    @staticmethod
    def _playwright_check(cdp_endpoint: str) -> str:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(cdp_endpoint)
            if not browser.contexts:
                raise RuntimeError("Chrome CDP has no browser context")
            page = browser.contexts[0].new_page()
            try:
                page.goto("https://api.ipify.org?format=json", wait_until="domcontentloaded")
                payload = json.loads(page.locator("body").inner_text())
                return str(payload["ip"])
            finally:
                page.close()

    def verify(self, cdp_endpoint: str, expected_full_ip: str | None = None) -> dict[str, Any]:
        try:
            full_ip = str(ipaddress.ip_address(self.checker(cdp_endpoint).strip()))
        except Exception as exc:
            return {"ok": False, "error_code": "CHROME_IP_UNAVAILABLE",
                    "error": type(exc).__name__}
        matches = expected_full_ip is None or full_ip == expected_full_ip
        return {"ok": matches, "full_ip": full_ip,
                "matches_expected": matches,
                "error_code": None if matches else "CHROME_IP_MISMATCH"}

