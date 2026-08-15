from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from .google_images import CONSENT_TEXT, EXPLICIT_CHALLENGE_TEXT
from .ranking import is_google_host


class StateCaptureError(RuntimeError):
    pass


def _is_google_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return is_google_host(host)


def _google_only_snapshot(snapshot: dict) -> dict:
    cookies = [
        cookie
        for cookie in snapshot.get("cookies", [])
        if is_google_host(str(cookie.get("domain", "")).lstrip("."))
    ]
    origins = [
        origin
        for origin in snapshot.get("origins", [])
        if _is_google_url(str(origin.get("origin", "")))
    ]
    return {"cookies": cookies, "origins": origins}


def sanitize_google_state_file(path: Path) -> dict[str, int]:
    """Atomically remove non-Google cookies/origins without exposing values."""
    path = path.resolve()
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    filtered = _google_only_snapshot(snapshot)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temp_path.replace(path)
    return {
        "cookies_before": len(snapshot.get("cookies", [])),
        "cookies_after": len(filtered["cookies"]),
        "origins_before": len(snapshot.get("origins", [])),
        "origins_after": len(filtered["origins"]),
    }


def classify_capture_page(url: str, visible_text: str) -> str:
    lowered_url = (url or "").lower()
    lowered_text = (visible_text or "").lower()
    if "/sorry/" in lowered_url or any(item in lowered_text for item in EXPLICIT_CHALLENGE_TEXT):
        return "challenge"
    if "consent.google." in lowered_url or any(item in lowered_text for item in CONSENT_TEXT):
        return "consent"
    return "normal"


def capture_google_state(cdp_endpoint: str, output_path: Path) -> dict[str, int | str]:
    """Capture state from a user-controlled Chrome without printing secret values."""
    output_path = output_path.resolve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_endpoint)
            if not browser.contexts:
                raise StateCaptureError("no Chrome context found at the CDP endpoint")

            context = browser.contexts[0]
            google_pages = [page for page in context.pages if _is_google_url(page.url)]
            if not google_pages:
                raise StateCaptureError("no Google page found; open Google manually before capture")

            normal_pages = []
            states = []
            for page in google_pages:
                try:
                    body = page.locator("body").inner_text(timeout=3000)
                except Exception:
                    body = ""
                state = classify_capture_page(page.url, body)
                states.append(state)
                if state == "normal":
                    normal_pages.append(page)

            if "challenge" in states:
                raise StateCaptureError("Google challenge detected; state was not saved")
            if "consent" in states:
                raise StateCaptureError("Google consent page detected; complete it manually before capture")
            if not normal_pages:
                raise StateCaptureError("no readable normal Google page found; state was not saved")

            snapshot = _google_only_snapshot(context.storage_state())
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
            temp_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temp_path.replace(output_path)

            return {
                "path": str(output_path),
                "cookie_count": len(snapshot.get("cookies", [])),
                "origin_count": len(snapshot.get("origins", [])),
                "google_page_count": len(google_pages),
            }
    except StateCaptureError:
        raise
    except Exception as exc:
        raise StateCaptureError(
            f"cannot capture state from {cdp_endpoint}: {type(exc).__name__}: {exc}"
        ) from exc
