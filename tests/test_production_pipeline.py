from types import SimpleNamespace

import pytest

from app.config import _as_bool
from app.google_images import ChallengeDetected, ConsentRequired, GoogleImagesBrowser
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
