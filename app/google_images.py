from __future__ import annotations
import time
from urllib.parse import urlencode
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from .config import Config
from .models import ImageItem
from .ranking import extract_external_from_href

CHALLENGE_PATTERNS = [
    "our systems have detected unusual traffic",
    "unusual traffic from your computer network",
    "i'm not a robot",
    "recaptcha",
    "automated queries",
]

class ChallengeDetected(RuntimeError):
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
        # udm=2 selects Google Images in current Google Search UI.
        params = {"q": keyword, "udm": "2", "hl": self.cfg.hl, "gl": self.cfg.gl}
        return self.cfg.base_url + "?" + urlencode(params)

    def _challenge_reason(self) -> str | None:
        if not self.page:
            return None
        url = (self.page.url or "").lower()
        if "/sorry/" in url:
            return f"challenge URL: {self.page.url}"
        try:
            text = self.page.locator("body").inner_text(timeout=5000).lower()
        except Exception:
            text = ""
        for pat in CHALLENGE_PATTERNS:
            if pat in text:
                return f"page text matched: {pat}"
        return None

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

        reason = self._challenge_reason()
        if reason:
            raise ChallengeDetected(reason)

        collected: list[ImageItem] = []
        seen_pages: set[str] = set()

        for round_idx in range(self.cfg.max_scroll_rounds + 1):
            reason = self._challenge_reason()
            if reason:
                raise ChallengeDetected(reason)

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

            if len(collected) >= max_results:
                break
            if round_idx >= self.cfg.max_scroll_rounds:
                break
            self.page.evaluate("pixels => window.scrollBy(0, pixels)", self.cfg.scroll_pixels)
            self.page.wait_for_timeout(self.cfg.scroll_wait_ms)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return url, collected[:max_results], elapsed_ms
