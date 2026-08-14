from app.state_capture import _is_google_url, classify_capture_page


def test_google_url_detection_supports_country_domains():
    assert _is_google_url("https://images.google.com/")
    assert _is_google_url("https://www.google.com.hk/search?q=test")
    assert _is_google_url("https://google.co.uk/")
    assert not _is_google_url("https://notgoogle.com/")


def test_capture_page_classification_is_fail_closed():
    assert classify_capture_page("https://www.google.com/search?q=test", "results") == "normal"
    assert classify_capture_page("https://www.google.com/sorry/index", "") == "challenge"
    assert classify_capture_page("https://consent.google.com/", "") == "consent"
    assert classify_capture_page("https://www.google.com/", "unusual traffic from your computer network") == "challenge"
