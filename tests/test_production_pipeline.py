from types import SimpleNamespace
import sys
from pathlib import Path

import pytest

from app.config import _as_bool
from app import main as main_module
from app.dashboard import (
    HUMAN_POLL_SECONDS,
    OperationManager,
    classify_cdp_page_urls,
    validate_history_url,
    validate_local_cdp_endpoint,
)
from app.google_images import (
    ChallengeDetected,
    ConsentRequired,
    GoogleImagesBrowser,
    wait_for_post_search_delay,
)
from app.google_images import domain_matches as diagnostic_domain_matches
from app.ranking_decision import decide_source_rank
from app.state_capture import _google_only_snapshot, _is_google_url
from app.structured_domains import SourceDomainObservation, parse_minimal_structured_domains


def resolved(rank: int, domain: str) -> SourceDomainObservation:
    return SourceDomainObservation(rank, (domain,), "resolved", "fixture")


def test_exact_hit_requires_all_preceding_positions_to_be_resolved():
    observations = [
        resolved(1, "one.example"),
        SourceDomainObservation(2, (), "missing", "fixture"),
        resolved(3, "target.example"),
    ]
    decision = decide_source_rank(observations, "target.example", 100, True)
    assert decision.result_code == -5
    assert "preceding" in (decision.message or "")


def test_exact_hit_does_not_require_positions_after_the_hit():
    observations = [resolved(1, "one.example"), resolved(2, "target.example")]
    decision = decide_source_rank(observations, "target.example", 100, True)
    assert decision.result_code == 2
    assert decision.matched_rank == 2


def test_absent_result_requires_complete_resolved_top_n():
    complete = [resolved(rank, f"source-{rank}.example") for rank in range(1, 101)]
    assert decide_source_rank(complete, "target.example", 100, True).result_code == -1
    assert decide_source_rank(complete[:-1], "target.example", 100, True).result_code == -5
    complete[49] = SourceDomainObservation(50, (), "missing", "fixture")
    assert decide_source_rank(complete, "target.example", 100, True).result_code == -5


def test_duplicate_source_urls_keep_distinct_card_ranks():
    href = "https://example.com/same-page"
    observations = parse_minimal_structured_domains("", [href, href])
    assert [item.rank for item in observations] == [1, 2]
    assert [item.source_url for item in observations] == [href, href]


@pytest.mark.parametrize("error", [ChallengeDetected("challenge"), ConsentRequired("consent")])
def test_search_preserves_safety_exceptions(error):
    browser = GoogleImagesBrowser.__new__(GoogleImagesBrowser)
    browser.page = object()
    browser.cfg = SimpleNamespace(
        base_url="https://www.google.com/search", hl="en", gl="us"
    )

    def fail(*_args):
        raise error

    browser._navigate_to_search = fail
    with pytest.raises(type(error)):
        browser.search("example", 100)


def test_state_capture_rejects_google_lookalikes_and_filters_other_sites():
    assert not _is_google_url("https://google.evil.com/")
    assert not _is_google_url("https://not.google.evil.com/")
    snapshot = {
        "cookies": [
            {"name": "google", "domain": ".google.com", "value": "kept"},
            {"name": "other", "domain": ".youtube.com", "value": "removed"},
        ],
        "origins": [
            {"origin": "https://images.google.com", "localStorage": []},
            {"origin": "https://example.com", "localStorage": [{"name": "private", "value": "x"}]},
        ],
    }
    filtered = _google_only_snapshot(snapshot)
    assert [item["name"] for item in filtered["cookies"]] == ["google"]
    assert [item["origin"] for item in filtered["origins"]] == ["https://images.google.com"]


def test_string_false_is_not_treated_as_true():
    assert _as_bool("false", "flag") is False
    assert _as_bool("true", "flag") is True
    with pytest.raises(ValueError):
        _as_bool("sometimes", "flag")


def test_source_diagnostic_has_domain_matcher_available():
    assert diagnostic_domain_matches("en.wikipedia.org", "wikipedia.org", True)


def test_manual_cdp_close_detaches_without_closing_user_browser():
    calls = {"context_close": 0, "browser_close": 0, "playwright_stop": 0}

    class Context:
        def close(self):
            calls["context_close"] += 1

    class Browser:
        def close(self):
            calls["browser_close"] += 1

    class Playwright:
        def stop(self):
            calls["playwright_stop"] += 1

    browser = GoogleImagesBrowser.__new__(GoogleImagesBrowser)
    browser.cfg = SimpleNamespace(
        session_mode="manual_cdp", persist_storage_state_updates=False
    )
    browser.context = Context()
    browser.browser = Browser()
    browser.pw = Playwright()
    browser.page = object()
    browser._attached_over_cdp = True
    browser._search_session_initialized = True
    browser.close()

    assert calls == {
        "context_close": 0,
        "browser_close": 0,
        "playwright_stop": 1,
    }


def test_source_test_default_post_search_delay_is_six_seconds(monkeypatch):
    captured = {}

    class Config:
        log_dir = Path(".")
        input_csv = Path("keywords.csv")

    class Browser:
        def __init__(self, _cfg):
            pass

        def start(self):
            return self

        def test_top_image_sources(self, _directory, _tasks, _maximum, _budget, delay):
            captured["delay"] = delay
            return {"process_exit_code": 0}, SimpleNamespace()

        def close(self):
            pass

    monkeypatch.setattr(sys, "argv", ["app", "--source-domain-test"])
    monkeypatch.setattr(main_module, "load_config", lambda _path: Config())
    monkeypatch.setattr(main_module, "load_tasks", lambda _path, _limit: [])
    monkeypatch.setattr(main_module, "GoogleImagesBrowser", Browser)
    monkeypatch.setattr("builtins.print", lambda *_args, **_kwargs: None)
    main_module.main()
    assert captured["delay"] == 6


def test_post_search_delay_starts_six_seconds_after_completion():
    now = [5.0]

    def sleep(seconds):
        now[0] += seconds

    assert wait_for_post_search_delay(5.0, 6.0, 20.0, lambda: now[0], sleep)
    assert now[0] >= 11.0


def test_post_search_delay_does_not_start_past_total_budget():
    assert not wait_for_post_search_delay(5.0, 6.0, 10.0, lambda: 5.0, lambda _seconds: None)


def test_dashboard_only_accepts_local_cdp_endpoints():
    assert validate_local_cdp_endpoint("http://127.0.0.1:9222/") == "http://127.0.0.1:9222"
    assert validate_local_cdp_endpoint("http://localhost:9223") == "http://localhost:9223"
    with pytest.raises(ValueError):
        validate_local_cdp_endpoint("http://example.com:9222")
    with pytest.raises(ValueError):
        validate_local_cdp_endpoint("https://127.0.0.1:9222")


def test_manual_history_navigation_only_accepts_http_urls():
    assert validate_history_url("https://images.google.com/ncr") == "https://images.google.com/ncr"
    with pytest.raises(ValueError):
        validate_history_url("javascript:alert(1)")
    with pytest.raises(ValueError):
        validate_history_url("file:///private/state.json")


def test_dashboard_checks_manual_verification_every_ten_seconds():
    assert HUMAN_POLL_SECONDS == 10.0


def test_refresh_page_binding_replaces_closed_challenge_target():
    class Page:
        def __init__(self, url, closed=False):
            self.url = url
            self.closed = closed
            self.navigation_timeout = None
            self.timeout = None

        def is_closed(self):
            return self.closed

        def set_default_navigation_timeout(self, value):
            self.navigation_timeout = value

        def set_default_timeout(self, value):
            self.timeout = value

    old_page = Page("https://www.google.com/sorry/index", closed=True)
    new_page = Page("https://www.google.com/search?q=done")
    browser = GoogleImagesBrowser(SimpleNamespace(navigation_timeout_ms=30000))
    browser.context = SimpleNamespace(pages=[new_page])
    browser.page = old_page

    assert browser.refresh_page_binding()
    assert browser.page is new_page
    assert new_page.navigation_timeout == 30000
    assert new_page.timeout == 10000


def test_refresh_page_binding_keeps_live_target():
    page = SimpleNamespace(
        url="https://www.google.com/search?q=current",
        is_closed=lambda: False,
    )
    browser = GoogleImagesBrowser(SimpleNamespace(navigation_timeout_ms=30000))
    browser.context = SimpleNamespace(pages=[page])
    browser.page = page

    assert not browser.refresh_page_binding()
    assert browser.page is page


def test_refresh_page_binding_prefers_new_normal_target_after_challenge():
    class Page:
        def __init__(self, url):
            self.url = url
            self.navigation_timeout = None
            self.timeout = None

        def is_closed(self):
            return False

        def set_default_navigation_timeout(self, value):
            self.navigation_timeout = value

        def set_default_timeout(self, value):
            self.timeout = value

    stale_challenge = Page("https://www.google.com/sorry/index")
    solved_target = Page("https://www.google.com/search?q=done")
    browser = GoogleImagesBrowser(SimpleNamespace(navigation_timeout_ms=30000))
    browser.context = SimpleNamespace(pages=[stale_challenge, solved_target])
    browser.page = stale_challenge

    assert browser.refresh_page_binding(prefer_normal_google_page=True)
    assert browser.page is solved_target


def test_dashboard_resume_start_marks_prior_samples_without_rerunning(monkeypatch):
    tasks = [SimpleNamespace(keyword=f"k{i}", target_domain="example.com") for i in range(1, 4)]
    monkeypatch.setattr("app.dashboard.load_tasks", lambda _path, _limit: tasks)
    dashboard = __import__("app.dashboard", fromlist=["ChromeSlot"])
    slot = dashboard.ChromeSlot(
        "test", "test", "http://127.0.0.1:9222", SimpleNamespace(input_csv=Path("input.csv"))
    )
    monkeypatch.setattr(slot, "_run", lambda *_args: None)

    slot.start_run(limit=3, max_results=100, post_delay=6, start_index=3)
    slot.worker.join(timeout=1)

    assert slot.completed_count == 2
    assert [task["status"] for task in slot.tasks] == ["resumed", "resumed", "pending"]


def test_human_wait_uses_cdp_state_then_reconnects_playwright(monkeypatch):
    dashboard = __import__("app.dashboard", fromlist=["ChromeSlot"])
    monkeypatch.setattr(dashboard, "HUMAN_POLL_SECONDS", 0)
    slot = dashboard.ChromeSlot(
        "test", "test", "http://127.0.0.1:9222", SimpleNamespace(input_csv=Path("input.csv"))
    )
    monkeypatch.setattr(slot, "_read_cdp_page_state", lambda: "normal")

    calls = []
    browser = SimpleNamespace(
        close=lambda: calls.append("close"),
        start=lambda: calls.append("start"),
        page_state=lambda: ("normal", None),
    )

    assert slot._wait_for_human(browser)
    assert calls == ["close", "start"]


def test_dashboard_classifies_cdp_tabs_without_query_details():
    assert classify_cdp_page_urls(["https://images.google.com/search?q=private"]) == "normal"
    assert classify_cdp_page_urls(["https://www.google.com/sorry/index?q=token"]) == "challenge"
    assert classify_cdp_page_urls(["https://consent.google.com/m"]) == "consent"
    assert classify_cdp_page_urls(["http://127.0.0.1:8765/"]) == "other"


def test_operation_manager_builds_allowlisted_argument_array():
    manager = OperationManager(Path.cwd(), Path("config.yaml"))
    command = manager._command(
        "diagnose",
        {
            "endpoint": "http://127.0.0.1:9222",
            "keyword": "Albert Einstein",
        },
    )

    assert command[0]
    assert command[1:3] == ["-m", "app.main"]
    assert "--diagnose" in command
    assert command[-2:] == ["--cdp-endpoint", "http://127.0.0.1:9222"]
    with pytest.raises(ValueError):
        manager._command("arbitrary_shell", {})


def test_dependency_install_operation_requires_explicit_confirmation():
    manager = OperationManager(Path.cwd(), Path("config.yaml"))
    with pytest.raises(ValueError):
        manager._command("install", {"endpoint": "http://127.0.0.1:9222"})
    command = manager._command(
        "install", {"endpoint": "http://127.0.0.1:9222", "confirmed": True}
    )
    assert command[1:4] == ["-m", "pip", "install"]
