from __future__ import annotations
from urllib.parse import urljoin, urlparse, parse_qs, unquote

GOOGLE_HOST_SUFFIXES = ("google.com", "googleusercontent.com", "gstatic.com", "ggpht.com")


def normalize_domain(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        host = urlparse(value).hostname or ""
    else:
        host = value.split("/", 1)[0]
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def hostname(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def is_google_host(host: str) -> bool:
    host = host.lower()
    return any(host == s or host.endswith("." + s) for s in GOOGLE_HOST_SUFFIXES)


def domain_matches(host: str, target: str, include_subdomains: bool = True) -> bool:
    host = normalize_domain(host)
    target = normalize_domain(target)
    if not host or not target:
        return False
    if host == target:
        return True
    return include_subdomains and host.endswith("." + target)


def extract_external_from_href(href: str, current_google_origin: str = "https://www.google.com") -> tuple[str | None, str | None]:
    """Return (source_page_url, image_url) from a Google Images anchor href.

    Handles legacy /imgres?imgrefurl=...&imgurl=..., Google redirect URLs,
    and direct external links. Does not depend on CSS class names.
    """
    def extract(value: str, depth: int, visited: set[str]) -> tuple[str | None, str | None]:
        if not value or depth > 4:
            return None, None
        value = unquote(value.strip())
        if value in visited:
            return None, None
        visited.add(value)
        if value.startswith(("/", "//")):
            value = urljoin(current_google_origin.rstrip("/") + "/", value)

        try:
            parsed = urlparse(value)
            query = parse_qs(parsed.query)
            image_url = unquote(query["imgurl"][0]) if query.get("imgurl") else None
            for key in ("imgrefurl", "url", "q"):
                for candidate in query.get(key, []):
                    candidate = unquote(candidate)
                    candidate_host = hostname(candidate)
                    if candidate_host and not is_google_host(candidate_host):
                        return candidate, image_url
                    nested_page, nested_image = extract(candidate, depth + 1, visited)
                    if nested_page:
                        return nested_page, image_url or nested_image

            value_host = hostname(value)
            if value_host and not is_google_host(value_host):
                return value, None
        except Exception:
            return None, None
        return None, None

    return extract(href, 0, set())
