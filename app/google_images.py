from __future__ import annotations
from collections import Counter
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from .config import Config
from .models import ImageItem, KeywordTask
from .ranking import domain_matches, hostname, is_google_host
from .structured_domains import SourceDomainObservation, parse_minimal_structured_domains

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


def is_expected_google_com_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().strip(".")
    return host == "google.com" or host.endswith(".google.com")


def probe_href_kind(href: str) -> str:
    """Classify a candidate href without retaining its query value."""
    try:
        parts = urlsplit(href)
        host = (parts.hostname or "").lower().strip(".")
        if not is_google_host(host) or parts.path != "/goto":
            return "other"
        value = dict(parse_qsl(parts.query, keep_blank_values=True)).get("url", "")
        if not value:
            return "google_goto_missing_target"
        target = urlsplit(value)
        if target.scheme in {"http", "https"} and target.hostname:
            return "google_goto_absolute_url"
        if target.query:
            return "google_goto_nested_url"
        return "google_goto_opaque_token"
    except Exception:
        return "invalid"

class ChallengeDetected(RuntimeError):
    pass

class ConsentRequired(RuntimeError):
    pass

class BrowserLaunchError(RuntimeError):
    pass

class NavigationError(RuntimeError):
    pass

class SearchParseTimeout(RuntimeError):
    pass


def wait_for_post_search_delay(
    previous_finished: float | None,
    delay_seconds: float,
    deadline: float,
    clock=time.monotonic,
    sleeper=time.sleep,
) -> bool:
    """Wait after completion; return False if the next start exceeds the budget."""
    if previous_finished is None:
        return True
    next_start = previous_finished + delay_seconds
    while True:
        now = clock()
        remaining = next_start - now
        if remaining <= 0:
            return True
        if now + remaining >= deadline:
            return False
        sleeper(min(remaining, 0.25))


class GoogleImagesBrowser:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.launch_args = ["--disable-notifications"]
        self._attached_over_cdp = False
        self._search_session_initialized = False
        self.last_source_observations: list[SourceDomainObservation] = []
        self.last_search_metrics = {
            "navigation_ms": 0,
            "results_wait_ms": 0,
            "parse_ms": 0,
            "search_parse_ms": 0,
        }

    def start(self):
        try:
            self.pw = sync_playwright().start()
            if self.cfg.session_mode == "manual_cdp":
                self.browser = self.pw.chromium.connect_over_cdp(self.cfg.cdp_endpoint)
                if not self.browser.contexts:
                    raise BrowserLaunchError(
                        f"no Chrome context available at {self.cfg.cdp_endpoint}"
                    )
                self._attached_over_cdp = True
                self.context = self.browser.contexts[0]
            elif self.cfg.session_mode == "storage_state":
                if not self.cfg.storage_state_path.is_file():
                    raise FileNotFoundError(
                        f"storage state not found: {self.cfg.storage_state_path}; "
                        "capture it manually before using session_mode=storage_state"
                    )
                self.browser = self.pw.chromium.launch(
                    channel=self.cfg.browser_channel,
                    headless=self.cfg.headless,
                    chromium_sandbox=True,
                    args=self.launch_args,
                )
                self.context = self.browser.new_context(
                    storage_state=str(self.cfg.storage_state_path),
                    viewport={"width": self.cfg.viewport_width, "height": self.cfg.viewport_height},
                )
            else:
                self.cfg.profile_dir.mkdir(parents=True, exist_ok=True)
                self.context = self.pw.chromium.launch_persistent_context(
                    user_data_dir=str(self.cfg.profile_dir),
                    channel=self.cfg.browser_channel,
                    headless=self.cfg.headless,
                    chromium_sandbox=True,
                    viewport={"width": self.cfg.viewport_width, "height": self.cfg.viewport_height},
                    args=self.launch_args,
                )
            google_pages = [
                page
                for page in self.context.pages
                if is_google_host(hostname(page.url))
            ]
            if self.cfg.session_mode == "manual_cdp" and google_pages:
                self.page = google_pages[-1]
                self.page.bring_to_front()
            else:
                self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.set_default_navigation_timeout(self.cfg.navigation_timeout_ms)
            self.page.set_default_timeout(10000)
            return self
        except Exception as exc:
            self.close()
            raise BrowserLaunchError(str(exc)) from exc

    def close(self):
        if (
            self.context
            and self.cfg.session_mode == "storage_state"
            and self.cfg.persist_storage_state_updates
        ):
            try:
                self.cfg.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
                self.context.storage_state(path=str(self.cfg.storage_state_path))
            except Exception:
                pass
        if not self._attached_over_cdp:
            try:
                if self.context:
                    self.context.close()
            except Exception:
                pass
            try:
                if self.browser:
                    self.browser.close()
            except Exception:
                pass
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            pass
        self.page = None
        self.context = None
        self.browser = None
        self.pw = None
        self._attached_over_cdp = False
        self._search_session_initialized = False

    def _build_url(self, keyword: str) -> str:
        params = {"q": keyword, "udm": "2", "hl": self.cfg.hl, "gl": self.cfg.gl}
        return self.cfg.base_url + "?" + urlencode(params)

    def _wait_for_result_candidates(self, expected_results: int) -> int:
        """Wait adaptively until the Top-N candidate count is stable."""
        started = time.perf_counter()
        deadline = started + self.cfg.results_load_wait_ms / 1000
        previous = -1
        stable_rounds = 0
        selector = 'a[href][target="_blank"]:has(img)'
        while time.perf_counter() < deadline:
            try:
                count = self.page.locator(selector).count()
            except Exception:
                count = 0
            if count >= expected_results and count == previous:
                stable_rounds += 1
                if stable_rounds >= 1:
                    break
            else:
                stable_rounds = 0
            previous = count
            self.page.wait_for_timeout(self.cfg.results_poll_interval_ms)
        return int((time.perf_counter() - started) * 1000)

    def _navigate_to_search(
        self, keyword: str, expected_results: int | None = None
    ) -> tuple[str, str | None]:
        """Navigate through the configured entry point and return target/landing URLs."""
        target_url = self._build_url(keyword)
        expected_results = expected_results or self.cfg.max_results
        navigation_timeout = min(
            self.cfg.navigation_timeout_ms, self.cfg.search_parse_timeout_ms
        )
        if self.cfg.search_navigation == "direct" or self._search_session_initialized:
            navigation_started = time.perf_counter()
            self.page.goto(
                target_url, wait_until="domcontentloaded", timeout=navigation_timeout
            )
            navigation_ms = int((time.perf_counter() - navigation_started) * 1000)
            wait_ms = self._wait_for_result_candidates(expected_results)
            self.last_search_metrics.update({
                "navigation_ms": navigation_ms,
                "results_wait_ms": wait_ms,
                "parse_ms": 0,
                "search_parse_ms": navigation_ms,
            })
            return target_url, None

        self.page.goto(
            self.cfg.images_home_url,
            wait_until="domcontentloaded",
            timeout=self.cfg.navigation_timeout_ms,
        )
        landing_url = self.page.url
        self._assert_normal_page()
        if self.cfg.require_google_com_host and not is_expected_google_com_host(landing_url):
            raise NavigationError(
                f"Images home redirected outside google.com: {redact_diagnostic_url(landing_url)}"
            )

        search_box = self.page.locator('textarea[name="q"], input[name="q"]').first
        search_box.wait_for(state="visible", timeout=10000)
        navigation_started = time.perf_counter()
        search_box.fill(keyword)
        search_box.press("Enter")
        self.page.wait_for_load_state("domcontentloaded", timeout=navigation_timeout)
        navigation_ms = int((time.perf_counter() - navigation_started) * 1000)
        self._search_session_initialized = True
        wait_ms = self._wait_for_result_candidates(expected_results)
        self.last_search_metrics.update({
            "navigation_ms": navigation_ms,
            "results_wait_ms": wait_ms,
            "parse_ms": 0,
            "search_parse_ms": navigation_ms,
        })
        return target_url, landing_url

    def result_dom_summary(self) -> dict[str, object]:
        """Return aggregate DOM counts without exposing result URLs or page text."""
        if not self.page:
            return {}
        try:
            return self.page.locator("a[href]").evaluate_all(
                r"""anchors => {
                    const out = {total_anchors: anchors.length, anchors_with_images: 0,
                                 external_anchors: 0, imgres_anchors: 0,
                                 url_redirect_anchors: 0, search_anchors: 0,
                                 anchor_attribute_names: {}, image_attribute_names: {},
                                 goto_url_param_count: 0, goto_absolute_url_count: 0,
                                 goto_relative_path_count: 0,
                                 goto_non_url_token_count: 0,
                                 goto_nested_query_key_count: 0};
                    const bump = (obj, key) => { obj[key] = (obj[key] || 0) + 1; };
                    for (const anchor of anchors) {
                        const image = anchor.querySelector('img');
                        if (image) {
                            out.anchors_with_images++;
                            for (const attr of anchor.attributes) bump(out.anchor_attribute_names, attr.name);
                            for (const attr of image.attributes) bump(out.image_attribute_names, attr.name);
                        }
                        let parsed;
                        try { parsed = new URL(anchor.href); } catch (_) { continue; }
                        const host = parsed.hostname.toLowerCase();
                        const isGoogle = host === 'google.com' || host.endsWith('.google.com');
                        if (!isGoogle) out.external_anchors++;
                        if (parsed.pathname.includes('imgres')) out.imgres_anchors++;
                        if (parsed.pathname === '/url') out.url_redirect_anchors++;
                        if (parsed.pathname === '/search') out.search_anchors++;
                        if (parsed.pathname === '/goto') {
                            const value = parsed.searchParams.get('url');
                            if (value) {
                                out.goto_url_param_count++;
                                try {
                                    const target = new URL(value);
                                    out.goto_absolute_url_count++;
                                } catch (_) {}
                                try {
                                    const nested = new URL(value, location.origin);
                                    const isAbsolute = /^[a-z][a-z0-9+.-]*:\/\//i.test(value);
                                    const nestedKeyCount = Array.from(nested.searchParams.keys()).length;
                                    if (!isAbsolute) {
                                        out.goto_relative_path_count++;
                                    }
                                    if (!isAbsolute && nestedKeyCount === 0) {
                                        out.goto_non_url_token_count++;
                                    }
                                    out.goto_nested_query_key_count += nestedKeyCount;
                                } catch (_) {}
                            }
                        }
                    }
                    return out;
                }"""
            )
        except Exception:
            return {}

    @staticmethod
    def _visible_external_domain_counts(page) -> Counter[str]:
        """Count visible external-link domains without returning full URLs."""
        try:
            hrefs = page.locator("a[href]:visible").evaluate_all(
                "anchors => anchors.map(anchor => anchor.href).filter(Boolean)"
            )
        except Exception:
            return Counter()
        domains: Counter[str] = Counter()
        for href in hrefs:
            domain = hostname(href)
            if domain and not is_google_host(domain):
                domains[domain] += 1
        return domains

    def probe_first_image(self, directory: Path, keyword: str) -> tuple[dict, Path]:
        """Click one visible image result and report only structural/domain observations."""
        if not self.page or not self.context:
            raise BrowserLaunchError("browser not started")

        timestamp = datetime.now().astimezone()
        report: dict[str, object] = {
            "generated_at": timestamp.isoformat(timespec="seconds"),
            "purpose": "Single-image structure probe; no challenge interaction or token capture.",
            "result_code": 0,
            "result_type": "PROBE_PENDING",
            "process_exit_code": 0,
            "privacy": {
                "keyword_recorded": False,
                "full_urls_recorded": False,
                "goto_token_recorded": False,
                "cookie_values_recorded": False,
            },
            "probe": {
                "candidate_found": False,
                "candidate_href_kind": None,
                "image_anchor_count": 0,
                "click_succeeded": False,
                "popup_count": 0,
                "popup_google_owned_count": 0,
                "popup_other_count": 0,
                "main_page_url_changed": False,
                "main_page_became_external": False,
                "dom_anchor_count_changed": False,
                "new_visible_external_domains": [],
                "popup_external_domains": [],
                "source_domains": [],
                "outcome": "pending",
            },
        }

        popup_pages = []
        main_page = self.page
        try:
            self._navigate_to_search(keyword)
            self._assert_normal_page()
            before_summary = self.result_dom_summary()
            before_domains = self._visible_external_domain_counts(main_page)
            before_url = main_page.url

            image_anchors = main_page.locator("a[href]:has(img)")
            candidate = None
            candidate_kind = None
            candidate_count = image_anchors.count()
            report["probe"]["image_anchor_count"] = candidate_count
            for index in range(min(candidate_count, 300)):
                current = image_anchors.nth(index)
                try:
                    href = current.evaluate("anchor => anchor.href || ''")
                    kind = probe_href_kind(href)
                    if kind.startswith("google_goto_") and current.is_visible():
                        candidate = current
                        candidate_kind = kind
                        break
                except Exception:
                    continue

            if candidate is None:
                report["result_code"] = -6
                report["result_type"] = "PROBE_IMAGE_CANDIDATE_NOT_FOUND"
                report["process_exit_code"] = 6
                report["probe"]["outcome"] = "no_candidate"
                return self._write_probe_report(directory, timestamp, report)

            report["probe"]["candidate_found"] = True
            report["probe"]["candidate_href_kind"] = candidate_kind
            existing_page_ids = {id(page) for page in self.context.pages}
            candidate.scroll_into_view_if_needed(timeout=5000)
            candidate.click(timeout=10000)
            report["probe"]["click_succeeded"] = True
            main_page.wait_for_timeout(3000)

            popup_pages = [page for page in self.context.pages if id(page) not in existing_page_ids]
            report["probe"]["popup_count"] = len(popup_pages)
            popup_external_domains: set[str] = set()
            popup_google_owned_count = 0
            popup_other_count = 0
            popup_challenge = False
            for popup in popup_pages:
                try:
                    popup.wait_for_load_state(
                        "domcontentloaded", timeout=min(self.cfg.navigation_timeout_ms, 8000)
                    )
                except Exception:
                    pass
                try:
                    popup.wait_for_timeout(750)
                    popup_url = popup.url or ""
                    popup_domain = hostname(popup_url)
                    if "/sorry/" in urlsplit(popup_url).path.lower() and is_google_host(popup_domain):
                        popup_challenge = True
                    if popup_domain and is_google_host(popup_domain):
                        popup_google_owned_count += 1
                    elif popup_domain:
                        popup_external_domains.add(popup_domain)
                    else:
                        popup_other_count += 1
                except Exception:
                    popup_other_count += 1

            main_domain = hostname(main_page.url)
            main_became_external = bool(main_domain and not is_google_host(main_domain))
            if not main_became_external:
                self._assert_normal_page()
            after_summary = self.result_dom_summary() if not main_became_external else {}
            after_domains = (
                self._visible_external_domain_counts(main_page)
                if not main_became_external else Counter()
            )
            increased_domains = sorted(
                domain for domain, count in after_domains.items()
                if count > before_domains.get(domain, 0)
            )
            source_domains = set(increased_domains) | popup_external_domains
            if main_became_external:
                source_domains.add(main_domain)

            report["probe"].update({
                "popup_google_owned_count": popup_google_owned_count,
                "popup_other_count": popup_other_count,
                "main_page_url_changed": main_page.url != before_url,
                "main_page_became_external": main_became_external,
                "dom_anchor_count_changed": (
                    before_summary.get("total_anchors") != after_summary.get("total_anchors")
                    if after_summary else False
                ),
                "new_visible_external_domains": increased_domains,
                "popup_external_domains": sorted(popup_external_domains),
                "source_domains": sorted(source_domains),
            })

            if popup_challenge:
                report["result_code"] = -4
                report["result_type"] = "GOOGLE_CHALLENGE_OR_UNUSUAL_TRAFFIC"
                report["process_exit_code"] = 4
                report["probe"]["outcome"] = "challenge"
            elif popup_external_domains:
                report["result_type"] = "PROBE_POPUP_EXTERNAL_DOMAIN_FOUND"
                report["probe"]["outcome"] = "popup_external_domain"
            elif main_became_external:
                report["result_type"] = "PROBE_MAIN_PAGE_EXTERNAL_DOMAIN_FOUND"
                report["probe"]["outcome"] = "main_page_external_domain"
            elif increased_domains:
                report["result_type"] = "PROBE_PANEL_EXTERNAL_DOMAIN_FOUND"
                report["probe"]["outcome"] = "panel_external_domain"
            elif report["probe"]["dom_anchor_count_changed"]:
                report["result_type"] = "PROBE_STRUCTURE_CHANGED_NO_DOMAIN"
                report["probe"]["outcome"] = "structure_changed_no_domain"
            else:
                report["result_type"] = "PROBE_NO_OBSERVABLE_SOURCE_DOMAIN"
                report["probe"]["outcome"] = "no_observable_source_domain"
        except ChallengeDetected:
            report["result_code"] = -4
            report["result_type"] = "GOOGLE_CHALLENGE_OR_UNUSUAL_TRAFFIC"
            report["process_exit_code"] = 4
            report["probe"]["outcome"] = "challenge"
        except ConsentRequired:
            report["result_code"] = -9
            report["result_type"] = "GOOGLE_CONSENT_REQUIRED"
            report["process_exit_code"] = 9
            report["probe"]["outcome"] = "consent"
        except (NavigationError, PlaywrightTimeoutError) as exc:
            report["result_code"] = -2
            report["result_type"] = "NETWORK_OR_NAVIGATION_ERROR"
            report["process_exit_code"] = 2
            report["probe"]["outcome"] = "navigation_error"
            report["probe"]["error_type"] = type(exc).__name__
        except Exception as exc:
            report["result_code"] = -6
            report["result_type"] = "RESULT_PARSE_ERROR"
            report["process_exit_code"] = 6
            report["probe"]["outcome"] = "probe_error"
            report["probe"]["error_type"] = type(exc).__name__
        finally:
            for popup in popup_pages:
                try:
                    popup.close()
                except Exception:
                    pass

        return self._write_probe_report(directory, timestamp, report)

    def _structured_source_observations(
        self, max_results: int, timeout_ms: int | None = None
    ) -> list[SourceDomainObservation]:
        """Parse source domains from the loaded page without clicking results."""
        if not self.page:
            return []
        if timeout_ms is not None:
            self.page.set_default_timeout(max(1, timeout_ms))
        try:
            candidates = self.page.locator('a[href][target="_blank"]:has(img)')
            hrefs = [
                str(href)
                for href in candidates.evaluate_all(
                    "anchors => anchors.map(anchor => anchor.href || '')"
                )[:max_results]
            ]

            # Fast path: direct result-card links retain one observation per card,
            # including repeated source URLs.
            if hrefs and all(
                hostname(href) and not is_google_host(hostname(href)) for href in hrefs
            ):
                return parse_minimal_structured_domains("", hrefs)

            records = candidates.evaluate_all(
                """anchors => anchors.map(anchor => {
                    const blocks = [];
                    let node = anchor;
                    for (let depth = 0; node && depth < 9; depth++, node = node.parentElement) {
                        const resultCount = node.querySelectorAll('a[href][target="_blank"]:has(img)').length;
                        if (resultCount > 1) break;
                        const html = node.outerHTML || '';
                        if (html && html.length <= 200000) blocks.push(html);
                    }
                    return {href: anchor.href || '', blocks};
                })"""
            )[:max_results]
            hrefs = [str(record.get("href", "")) for record in records]
            blocks = [list(record.get("blocks", [])) for record in records]
            return parse_minimal_structured_domains(self.page.content(), hrefs, blocks)
        finally:
            if timeout_ms is not None:
                self.page.set_default_timeout(10000)

    def test_top_image_sources(
        self,
        directory: Path,
        tasks: list[KeywordTask],
        max_results: int,
        time_budget_seconds: float,
        post_search_delay_seconds: float = 0,
    ) -> tuple[dict, Path]:
        """Run a DOM-only bounded source-domain test over multiple samples."""
        if not self.page or not self.context:
            raise BrowserLaunchError("browser not started")
        timestamp = datetime.now().astimezone()
        started = time.monotonic()
        deadline = started + time_budget_seconds
        sample_reports: list[dict[str, object]] = []
        stopped_reason = None
        previous_sample_started: float | None = None
        previous_sample_finished: float | None = None

        for sample_index, task in enumerate(tasks, start=1):
            if previous_sample_finished is not None:
                if not wait_for_post_search_delay(
                    previous_sample_finished,
                    post_search_delay_seconds,
                    deadline,
                ):
                    stopped_reason = "time_budget_exhausted"
                    break
            if time.monotonic() >= deadline:
                stopped_reason = "time_budget_exhausted"
                break
            sample_started = time.monotonic()
            remaining_samples = len(tasks) - sample_index + 1
            fair_share_seconds = max(
                5.0,
                (deadline - sample_started) / max(remaining_samples, 1),
            )
            sample_deadline = min(deadline, sample_started + fair_share_seconds)
            sample = {
                "sample_index": sample_index,
                "start_offset_ms": round((sample_started - started) * 1000),
                "start_gap_ms": (
                    round((sample_started - previous_sample_started) * 1000)
                    if previous_sample_started is not None else None
                ),
                "previous_finish_to_start_ms": (
                    round((sample_started - previous_sample_finished) * 1000)
                    if previous_sample_finished is not None else None
                ),
                "target_domain": task.target_domain,
                "candidate_count": 0,
                "attempted_count": 0,
                "resolved_count": 0,
                "matched_rank": None,
                "matched_domain": None,
                "candidate_match_rank": None,
                "candidate_match_domain": None,
                "candidate_match_status": None,
                "unresolved_before_candidate_match": 0,
                "status": "pending",
                "resolution_methods": {},
                "elapsed_ms": None,
                "navigation_ms": None,
                "results_wait_ms": None,
                "parse_ms": None,
                "search_parse_ms": None,
                "sample_time_budget_seconds": round(fair_share_seconds, 3),
            }
            previous_sample_started = sample_started
            try:
                self._navigate_to_search(task.keyword, max_results)
                self._assert_normal_page()
                sample["navigation_ms"] = self.last_search_metrics["navigation_ms"]
                sample["results_wait_ms"] = self.last_search_metrics["results_wait_ms"]
                if self.last_search_metrics["navigation_ms"] > self.cfg.search_parse_timeout_ms:
                    sample["status"] = "search_parse_timeout"
                    continue
                parse_started = time.perf_counter()
                observations = self._structured_source_observations(max_results)
                parse_ms = int((time.perf_counter() - parse_started) * 1000)
                search_parse_ms = self.last_search_metrics["navigation_ms"] + parse_ms
                self.last_search_metrics.update({
                    "parse_ms": parse_ms,
                    "search_parse_ms": search_parse_ms,
                })
                sample["parse_ms"] = parse_ms
                sample["search_parse_ms"] = search_parse_ms
                candidate_count = len(observations)
                sample["candidate_count"] = candidate_count
                if search_parse_ms > self.cfg.search_parse_timeout_ms:
                    sample["status"] = "search_parse_timeout"
                    continue
                methods: Counter[str] = Counter()

                unresolved_ranks: list[int] = []
                candidate_match: tuple[int, str, str] | None = None
                for observation in observations:
                    sample["attempted_count"] += 1
                    methods[f"{observation.method}:{observation.status}"] += 1
                    if observation.status == "resolved":
                        sample["resolved_count"] += 1
                    else:
                        unresolved_ranks.append(observation.rank)
                    matched = next(
                        (domain for domain in observation.domains
                         if domain_matches(domain, task.target_domain, self.cfg.include_subdomains)),
                        None,
                    )
                    if matched and candidate_match is None:
                        candidate_match = (observation.rank, matched, observation.status)

                if candidate_match:
                    candidate_rank, candidate_domain, candidate_status = candidate_match
                    unresolved_before = sum(rank < candidate_rank for rank in unresolved_ranks)
                    sample["candidate_match_rank"] = candidate_rank
                    sample["candidate_match_domain"] = candidate_domain
                    sample["candidate_match_status"] = candidate_status
                    sample["unresolved_before_candidate_match"] = unresolved_before
                    if unresolved_before == 0 and candidate_status == "resolved":
                        sample["matched_rank"] = candidate_rank
                        sample["matched_domain"] = candidate_domain
                        sample["status"] = "found"
                    else:
                        sample["status"] = "candidate_match_with_unresolved_predecessors"

                if sample["status"] == "pending":
                    if candidate_count < max_results:
                        sample["status"] = "incomplete_candidate_depth"
                    elif sample["resolved_count"] < max_results:
                        sample["status"] = "incomplete_resolution"
                    else:
                        sample["status"] = "not_found_in_top_n"
                sample["resolution_methods"] = dict(sorted(methods.items()))
            except ChallengeDetected:
                sample["status"] = "challenge"
                stopped_reason = "challenge"
            except ConsentRequired:
                sample["status"] = "consent"
                stopped_reason = "consent"
            except PlaywrightTimeoutError:
                sample["navigation_ms"] = self.cfg.search_parse_timeout_ms
                sample["search_parse_ms"] = self.cfg.search_parse_timeout_ms
                sample["status"] = "search_parse_timeout"
            except Exception as exc:
                sample["status"] = "error"
                sample["error_type"] = type(exc).__name__
            finally:
                sample["elapsed_ms"] = int((time.monotonic() - sample_started) * 1000)
                sample_reports.append(sample)
                previous_sample_finished = time.monotonic()
            if stopped_reason in {"challenge", "consent", "time_budget_exhausted"}:
                break

        elapsed_seconds = round(time.monotonic() - started, 3)
        statuses = Counter(str(sample["status"]) for sample in sample_reports)
        total_attempted = sum(int(sample["attempted_count"]) for sample in sample_reports)
        total_resolved = sum(int(sample["resolved_count"]) for sample in sample_reports)
        measured = [
            sample for sample in sample_reports if sample.get("search_parse_ms") is not None
        ]
        if stopped_reason == "challenge":
            result_code, result_type, process_exit_code = -4, "GOOGLE_CHALLENGE_OR_UNUSUAL_TRAFFIC", 4
        elif stopped_reason == "consent":
            result_code, result_type, process_exit_code = -9, "GOOGLE_CONSENT_REQUIRED", 9
        elif stopped_reason == "time_budget_exhausted" or len(sample_reports) < len(tasks):
            result_code, result_type, process_exit_code = -5, "SOURCE_TEST_TIME_BUDGET_EXHAUSTED", 5
        elif any(sample["status"] not in {"found", "not_found_in_top_n"} for sample in sample_reports):
            result_code, result_type, process_exit_code = -5, "SOURCE_TEST_INCOMPLETE", 5
        else:
            result_code, result_type, process_exit_code = 0, "SOURCE_TEST_COMPLETE", 0

        report = {
            "generated_at": timestamp.isoformat(timespec="seconds"),
            "purpose": "Bounded top-N image source-domain test; no challenge bypass.",
            "result_code": result_code,
            "result_type": result_type,
            "process_exit_code": process_exit_code,
            "limits": {
                "sample_limit": len(tasks),
                "max_results_per_sample": max_results,
                "time_budget_seconds": time_budget_seconds,
                "post_search_delay_seconds": post_search_delay_seconds,
                "search_parse_timeout_ms": self.cfg.search_parse_timeout_ms,
                "sequential_only": True,
                "interaction_mode": "dom_only",
                "result_clicks": 0,
            },
            "privacy": {
                "keywords_recorded": False,
                "full_urls_recorded": False,
                "goto_tokens_recorded": False,
                "cookie_values_recorded": False,
            },
            "summary": {
                "samples_started": len(sample_reports),
                "samples_requested": len(tasks),
                "statuses": dict(sorted(statuses.items())),
                "total_attempted": total_attempted,
                "total_resolved": total_resolved,
                "resolution_rate": (
                    round(total_resolved / total_attempted, 4) if total_attempted else 0.0
                ),
                "average_search_parse_ms": (
                    round(sum(int(sample["search_parse_ms"]) for sample in measured) / len(measured), 1)
                    if measured else None
                ),
                "average_results_wait_ms": (
                    round(sum(int(sample["results_wait_ms"] or 0) for sample in measured) / len(measured), 1)
                    if measured else None
                ),
                "elapsed_seconds": elapsed_seconds,
                "stopped_reason": stopped_reason,
            },
            "samples": sample_reports,
        }
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("source_domain_test_" + timestamp.strftime("%Y%m%d_%H%M%S") + ".json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report, path

    @staticmethod
    def _write_probe_report(directory: Path, timestamp: datetime, report: dict) -> tuple[dict, Path]:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("first_image_probe_" + timestamp.strftime("%Y%m%d_%H%M%S") + ".json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report, path

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
            safe_url = redact_diagnostic_url(self.page.url)
            txt.write_text(f"URL: {safe_url}\nTITLE: {title}\n\nVISIBLE BODY:\n{body}\n", encoding="utf-8")
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
        present_locks = (
            [name for name in lock_names if (self.cfg.profile_dir / name).exists()]
            if self.cfg.session_mode == "persistent_profile" else []
        )
        report = {
            "generated_at": timestamp.isoformat(timespec="seconds"),
            "purpose": "Read-only browser environment comparison; browser launch failed.",
            "playwright_version": _installed_version("playwright"),
            "browser": {
                "channel": self.cfg.browser_channel,
                "session_mode": self.cfg.session_mode,
                "profile_path_configured": (
                    str(self.cfg.profile_dir) if self.cfg.session_mode == "persistent_profile" else None
                ),
                "storage_state_path_configured": (
                    str(self.cfg.storage_state_path) if self.cfg.session_mode == "storage_state" else None
                ),
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
        landing_url = None
        navigation_error = None
        try:
            target_url, landing_url = self._navigate_to_search(keyword)
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
                "session_mode": self.cfg.session_mode,
                "profile_path_configured": (
                    str(self.cfg.profile_dir) if self.cfg.session_mode == "persistent_profile" else None
                ),
                "storage_state_path_configured": (
                    str(self.cfg.storage_state_path) if self.cfg.session_mode == "storage_state" else None
                ),
                "profile_path_reported_by_chrome": chrome["profile_path"],
                "headless": self.cfg.headless,
                "chromium_sandbox": True,
                "configured_launch_args": list(self.launch_args),
                "command_line_reported_by_chrome": chrome["command_line"],
            },
            "page": {
                "diagnostic_keyword": keyword,
                "search_navigation": self.cfg.search_navigation,
                "images_home_url": self.cfg.images_home_url,
                "images_home_landing_url": redact_diagnostic_url(landing_url) if landing_url else None,
                "requested_url": target_url,
                "current_url": redact_diagnostic_url(self.page.url),
                "title": title,
                "state": state,
                "state_reason": reason,
                "navigation_error": navigation_error,
                "challenge_detected": state == "challenge",
                "consent_detected": state == "consent",
                "result_dom_summary": self.result_dom_summary(),
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
        try:
            url, _ = self._navigate_to_search(keyword, max_results)
        except (ChallengeDetected, ConsentRequired):
            raise
        except PlaywrightTimeoutError:
            raise SearchParseTimeout("search navigation exceeded the configured 5-second budget") from None
        except Exception as exc:
            raise NavigationError(str(exc)) from exc

        self._assert_normal_page()
        if self.last_search_metrics["navigation_ms"] > self.cfg.search_parse_timeout_ms:
            raise SearchParseTimeout("search navigation exceeded the configured 5-second budget")

        remaining_ms = self.cfg.search_parse_timeout_ms - self.last_search_metrics["navigation_ms"]
        if remaining_ms <= 0:
            raise SearchParseTimeout("search navigation exhausted the configured processing budget")

        parse_started = time.perf_counter()
        try:
            observations = self._structured_source_observations(max_results, remaining_ms)
        except PlaywrightTimeoutError:
            raise SearchParseTimeout("source-domain parsing exceeded the configured processing budget") from None
        self.last_source_observations = observations
        collected = [
            ImageItem(
                rank=item.rank,
                page_url=item.source_url or f"https://{item.domains[0]}/",
            )
            for item in observations
            if item.status == "resolved" and len(item.domains) == 1
        ]

        parse_ms = int((time.perf_counter() - parse_started) * 1000)
        search_parse_ms = self.last_search_metrics["navigation_ms"] + parse_ms
        self.last_search_metrics.update({
            "parse_ms": parse_ms,
            "search_parse_ms": search_parse_ms,
        })
        if search_parse_ms > self.cfg.search_parse_timeout_ms:
            raise SearchParseTimeout("search and parsing exceeded the configured 5-second budget")
        return self.page.url, collected[:max_results], search_parse_ms
