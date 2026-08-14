from app.ranking import domain_matches, extract_external_from_href
from app.google_images import infer_google_login

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

def test_google_login_hint_does_not_require_cookie_values():
    assert infer_google_login({"NID", "SOCS"}) == "not_detected"
    assert infer_google_login({"NID", "__Secure-1PSID"}) == "likely_signed_in"
