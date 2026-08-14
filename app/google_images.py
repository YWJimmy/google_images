from __future__ import annotations
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from .config import Config
from .models import ImageItem
from .ranking import extract_external_from_href

# Deliberately specific phrases only. Do NOT use generic "recaptcha" substring
# matching because normal Google pages can contain that word in non-challenge UI.
EXPLICIT_CHALLENGE_TEXT = [
    "our systems have detected unusual traffic from your computer network",
    "unusual traffic from your computer network",
    "your computer or network may be sending automated queries",
    "to continue, please type the characters below",
]

CONSENT_TEXT = [
    "before you continue to google",
    "before you continue to google search",
]

GOOGLE_LOGIN_COOKIE_NAMES = {
    "SID", "HSID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID",
}


def infer_google_login(cookie_names: set[str]) -> str:
    """Return a conservative login hint without exposing cookie values."""
    return "likely_signed_in" if cookie_names & GOOGLE_LOGIN_COOKIE_NAMES else "not_detected"


def _installed_version(distribution: str) -> str:
    try:
        return package_version(distribution)
    except PackageNotFoundError:
        return "unknown"


def redact_diagnostic_url(url: str) -> str:
    """Redact opaque challenge tokens while retaining useful URL context."""
    try:
        parts = urlsplit(url)
        if "/sorry/" not in parts.path.lower():
            return url
        query = [(key, "<redacted>" if key.lower() == "q" else value)
                 for key, value in parse_qsl(parts.query, keep_blank_values=True)]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return url


def diagnostic_outcome(state: str, navigation_error: str | None) -> tuple[int, str, int]:
    """Return application result code, type, and process exit code."""
    if state == "challenge":
        return -4, "GOOGLE_CHALLENGE_OR_UNUSUAL_TRAFFIC", 4
    if state == "consent":
        return -9, "GOOGLE_CONSENT_REQUIRED", 9
    if navigation_error:
        return -2, "NETWORK_OR_NAVIGATION_ERROR", 2
    return 0, "DIAGNOSTIC_NORMAL", 0

class ChallengeDetected(RuntimeError):
    pass

class ConsentRequired(RuntimeError):
    pass

class BrowserLaunchError(RuntimeError):
    pass

class NavigationError(RuntimeError):
    pass

class GoogleImagesBrowser:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pw = None
        self.context = None
        self.page = None
        self.launch_args = ["--disable-notifications"]

    def start(self):
        try:
            self.cfg.profile_dir.mkdir(parents=True, exist_ok=True)
            self.pw = sync_playwright().start()
            self.context = self.pw.chromium.launch_persistent_context(
                user_data_dir=str(self.cfg.profile_dir),
                channel=self.cfg.browser_channel,
                headless=self.cfg.headless,
                chromium_sandbox=True,
                viewport={"width": self.cfg.viewport_width, "height": self.cfg.viewport_height},
                args=self.launch_args,
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_navigation_timeout(self.cfg.navigation_timeout_ms)
            self.page.set_default_timeout(10000)
            return self
        except Exception as exc:
            self.close()
            raise BrowserLaunchError(str(exc)) from exc

    def close(self):
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            pass
        self.page = None
        self.context = None
        self.pw = None

    def _build_url(self, keyword: str) -> str:
        params = {"q": keyword, "udm": "2", "hl": self.cfg.hl, "gl": self.cfg.gl}
        return self.cfg.base_url + "?" + urlencode(params)

    def _visible_body_text(self) -> str:
        try:
            return self.page.locator("body").inner_text(timeout=5000).lower() if self.page else ""
        except Exception:
            return ""

    def _page_state(self) -> tuple[str, str | None]:
        """Return (state, reason), where state is normal/challenge/consent."""
        if not self.page:
            return "normal", None

        url = (self.page.url or "").lower()
        if "/sorry/" in url:
            return "challenge", f"challenge URL: {redact_diagnostic_url(self.page.url)}"

        # Visible reCAPTCHA UI is strong evidence. Merely having reCAPTCHA code or
        # hidden markup in the document is NOT considered a challenge.
        try:
            iframe = self.page.locator('iframe[src*="recaptcha" i], iframe[title*="recaptcha" i]')
            for i in range(min(iframe.count(), 5)):
                try:
                    if iframe.nth(i).is_visible():
                        return "challenge", "visible reCAPTCHA iframe"
                except Exception:
                    pass
        except Exception:
            pass

        text = self._visible_body_text()
        for pat in EXPLICIT_CHALLENGE_TEXT:
            if pat in text:
                return "challenge", f"visible page text matched: {pat}"

        if "consent.google." in url:
            return "consent", f"Google consent URL: {self.page.url}"
        for pat in CONSENT_TEXT:
            if pat in text:
                return "consent", f"visible consent text matched: {pat}"

        return "normal", None

    def save_diagnostics(self, directory: Path, prefix: str) -> list[str]:
        """Best-effort diagnostic capture. Does not interact with any challenge."""
        if not self.page:
            return []
        directory.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in prefix)[:80]
        paths = []
        try:
            png = directory / f"{safe}.png"
            self.page.screenshot(path=str(png), full_page=False)
            paths.append(str(png))
        except Exception:
            pass
        try:
            txt = directory / f"{safe}.txt"
            title = self.page.title()
            body = self._visible_body_text()[:12000]
            txt.write_text(f"URL: {self.page.url}\nTITLE: {title}\n\nVISIBLE BODY:\n{body}\n", encoding="utf-8")
            paths.append(str(txt))
        except Exception:
            pass
        return paths

    def _chrome_version_details(self) -> dict[str, str | None]:
        """Read Chrome's own version page; failure must not abort diagnosis."""
        if not self.context:
            return {"version": None, "profile_path": None, "command_line": None}
        version_page = None
        try:
            version_page = self.context.new_page()
            version_page.goto("chrome://version/", wait_until="domcontentloaded")

            def value(selector: str) -> str | None:
                try:
                    text = version_page.locator(selector).inner_text(timeout=3000).strip()
                    return text or None
                except Exception:
                    return None

            return {
                "version": value("#version"),
                "profile_path": value("#profile_path"),
                "command_line": value("#command_line"),
            }
        except Exception:
            return {"version": None, "profile_path": None, "command_line": None}
        finally:
            if version_page:
                try:
                    version_page.close()
                except Exception:
                    pass

    def save_launch_failure_diagnostic(self, directory: Path, error: Exception) -> Path:
        """Persist startup failures that occur before a page can be inspected."""
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone()
        lock_names = ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket")
        present_locks = [name for name in lock_names if (self.cfg.profile_dir / name).exists()]
        report = {
            "generated_at": timestamp.isoformat(timespec="seconds"),
            "purpose": "Read-only browser environment comparison; browser launch failed.",
            "playwright_version": _installed_version("playwright"),
            "browser": {
                "channel": self.cfg.browser_channel,
                "profile_path_configured": str(self.cfg.profile_dir),
                "headless": self.cfg.headless,
                "chromium_sandbox": True,
                "configured_launch_args": list(self.launch_args),
            },
            "launch": {
                "succeeded": False,
                "error_type": type(error).__name__,
                "error": str(error),
                "profile_lock_candidates_present": present_locks,
                "hint": (
                    "Close every Chrome window using the dedicated profile before retrying. "
                    "Lock-file presence is only a clue and may be stale; it is not proof that Chrome is running."
                ),
            },
            "session_summary": {
                "cookie_values_recorded": False,
                "page_or_session_inspected": False,
            },
        }
        path = directory / ("launch_failure_" + timestamp.strftime("%Y%m%d_%H%M%S") + ".json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def diagnose(self, directory: Path, keyword: str = "Albert Einstein") -> tuple[dict, Path]:
        """Capture a read-only environment report without bypassing challenges."""
        if not self.page or not self.context:
            raise BrowserLaunchError("browser not started")

        chrome = self._chrome_version_details()
        target_url = self._build_url(keyword)
        navigation_error = None
        try:
            self.page.goto(target_url, wait_until="domcontentloaded")
        except Exception as exc:
            navigation_error = f"{type(exc).__name__}: {exc}"

        state, reason = self._page_state()
        result_code, result_type, process_exit_code = diagnostic_outcome(state, navigation_error)
        try:
            title = self.page.title()
        except Exception:
            title = None
        try:
            navigator = self.page.evaluate(
                """() => ({
                    userAgent: navigator.userAgent,
                    platform: navigator.platform,
                    language: navigator.language,
                    languages: navigator.languages,
                    webdriver: navigator.webdriver
                })"""
            )
        except Exception:
            navigator = {}

        try:
            google_cookies = self.context.cookies(["https://www.google.com"])
        except Exception:
            google_cookies = []
        cookie_names = {str(cookie.get("name", "")) for cookie in google_cookies}

        try:
            origins = self.context.storage_state().get("origins", [])
        except Exception:
            origins = []
        local_storage_entries = sum(len(origin.get("localStorage", [])) for origin in origins)

        timestamp = datetime.now().astimezone()
        report = {
            "generated_at": timestamp.isoformat(timespec="seconds"),
            "purpose": "Read-only browser environment comparison; no challenge interaction or bypass.",
            "result_code": result_code,
            "result_type": result_type,
            "process_exit_code": process_exit_code,
            "playwright_version": _installed_version("playwright"),
            "browser": {
                "channel": self.cfg.browser_channel,
                "version": chrome["version"],
                "profile_path_configured": str(self.cfg.profile_dir),
                "profile_path_reported_by_chrome": chrome["profile_path"],
                "headless": self.cfg.headless,
                "chromium_sandbox": True,
                "configured_launch_args": list(self.launch_args),
                "command_line_reported_by_chrome": chrome["command_line"],
            },
            "page": {
                "diagnostic_keyword": keyword,
                "requested_url": target_url,
                "current_url": redact_diagnostic_url(self.page.url),
                "title": title,
                "state": state,
                "state_reason": reason,
                "navigation_error": navigation_error,
                "challenge_detected": state == "challenge",
                "consent_detected": state == "consent",
            },
            "navigator": navigator,
            "session_summary": {
                "google_cookie_count": len(google_cookies),
                "google_login_hint": infer_google_login(cookie_names),
                "google_login_hint_basis": "Known Google auth-cookie names only; this is not proof of login.",
                "storage_origin_count": len(origins),
                "local_storage_entry_count": local_storage_entries,
                "cookie_values_recorded": False,
            },
        }

        directory.mkdir(parents=True, exist_ok=True)
        prefix = "environment_" + timestamp.strftime("%Y%m%d_%H%M%S")
        report_path = directory / f"{prefix}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["artifacts"] = self.save_diagnostics(directory, prefix)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report, report_path

    def _assert_normal_page(self):
        state, reason = self._page_state()
        if state == "challenge":
            raise ChallengeDetected(reason or "Google challenge detected")
        if state == "consent":
            raise ConsentRequired(reason or "Google consent required")

    def search(self, keyword: str, max_results: int) -> tuple[str, list[ImageItem], int]:
        if not self.page:
            raise BrowserLaunchError("browser not started")
        url = self._build_url(keyword)
        started = time.perf_counter()
        try:
            self.page.goto(url, wait_until="domcontentloaded")
        except PlaywrightTimeoutError as exc:
            raise NavigationError(f"navigation timeout: {exc}") from exc
        except Exception as exc:
            raise NavigationError(str(exc)) from exc

        self._assert_normal_page()

        collected: list[ImageItem] = []
        seen_pages: set[str] = set()

        for round_idx in range(self.cfg.max_scroll_rounds + 1):
            self._assert_normal_page()
            try:
                hrefs = self.page.locator("a[href]").evaluate_all(
                    "els => els.map(a => a.href).filter(Boolean)"
                )
            except Exception as exc:
                raise NavigationError(f"failed to inspect result links: {exc}") from exc

            for href in hrefs:
                page_url, image_url = extract_external_from_href(href, "https://www.google.com")
                if not page_url or page_url in seen_pages:
                    continue
                seen_pages.add(page_url)
                collected.append(ImageItem(rank=len(collected) + 1, page_url=page_url, image_url=image_url))
                if len(collected) >= max_results:
                    break

            if len(collected) >= max_results or round_idx >= self.cfg.max_scroll_rounds:
                break
            self.page.evaluate("pixels => window.scrollBy(0, pixels)", self.cfg.scroll_pixels)
            self.page.wait_for_timeout(self.cfg.scroll_wait_ms)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return url, collected[:max_results], elapsed_ms
