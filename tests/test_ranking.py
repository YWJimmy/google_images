from app.ranking import domain_matches, extract_external_from_href, is_google_host
from app.google_images import diagnostic_outcome, infer_google_login, is_expected_google_com_host, redact_diagnostic_url

def test_domain_matches():
    assert domain_matches("en.wikipedia.org", "wikipedia.org", True)
    assert not domain_matches("notwikipedia.org", "wikipedia.org", True)

def test_legacy_imgres():
    href = "https://www.google.com/imgres?imgurl=https%3A%2F%2Fcdn.example.com%2Fa.jpg&imgrefurl=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FCat"
    page, image = extract_external_from_href(href)
    assert page == "https://en.wikipedia.org/wiki/Cat"
    assert image == "https://cdn.example.com/a.jpg"

def test_direct_external():
    page, image = extract_external_from_href("https://example.com/page")
    assert page == "https://example.com/page"
    assert image is None

def test_google_country_domains_are_not_external_sources():
    for host in (
        "google.cn",
        "images.google.cn",
        "google.com.hk",
        "www.google.com.au",
        "google.co.uk",
        "images.google.co.jp",
    ):
        assert is_google_host(host)

    for href in (
        "https://www.google.com.hk/intl/en/about/products",
        "https://images.google.co.uk/search?q=test",
    ):
        assert extract_external_from_href(href) == (None, None)

def test_google_lookalike_domains_remain_external():
    for host in ("notgoogle.com", "google.evil.com", "google.com.evil.net"):
        assert not is_google_host(host)

def test_nested_google_goto_redirect():
    href = (
        "https://www.google.com/goto?url="
        "%2Furl%3Fsa%3Di%26url%3Dhttps%253A%252F%252Fexample.com%252Fsource-page"
    )
    page, image = extract_external_from_href(href)
    assert page == "https://example.com/source-page"
    assert image is None

def test_nested_redirect_loop_is_bounded():
    page, image = extract_external_from_href("https://www.google.com/goto?url=%2Fgoto%3Furl%3D%252Fgoto")
    assert page is None
    assert image is None

def test_google_login_hint_does_not_require_cookie_values():
    assert infer_google_login({"NID", "SOCS"}) == "not_detected"
    assert infer_google_login({"NID", "__Secure-1PSID"}) == "likely_signed_in"

def test_diagnostic_outcome_maps_states_to_process_exit_codes():
    assert diagnostic_outcome("normal", None) == (0, "DIAGNOSTIC_NORMAL", 0)
    assert diagnostic_outcome("normal", "timeout") == (-2, "NETWORK_OR_NAVIGATION_ERROR", 2)
    assert diagnostic_outcome("challenge", None) == (-4, "GOOGLE_CHALLENGE_OR_UNUSUAL_TRAFFIC", 4)
    assert diagnostic_outcome("consent", None) == (-9, "GOOGLE_CONSENT_REQUIRED", 9)

def test_challenge_url_token_is_redacted():
    url = "https://www.google.com/sorry/index?continue=https%3A%2F%2Fgoogle.com&q=secret-token&hl=en"
    redacted = redact_diagnostic_url(url)
    assert "secret-token" not in redacted
    assert "%3Credacted%3E" in redacted

def test_google_com_host_check_rejects_country_redirects():
    assert is_expected_google_com_host("https://images.google.com/ncr")
    assert is_expected_google_com_host("https://www.google.com/search")
    assert not is_expected_google_com_host("https://images.google.com.hk/")
