from __future__ import annotations
import time
from pathlib import Path
from urllib.parse import urlencode
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

    def start(self):
        try:
            self.cfg.profile_dir.mkdir(parents=True, exist_ok=True)
            self.pw = sync_playwright().start()
            self.context = self.pw.chromium.launch_persistent_context(
                user_data_dir=str(self.cfg.profile_dir),
                channel=self.cfg.browser_channel,
                headless=self.cfg.headless,
                viewport={"width": self.cfg.viewport_width, "height": self.cfg.viewport_height},
                args=["--disable-notifications"],
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
            return "challenge", f"challenge URL: {self.page.url}"

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
