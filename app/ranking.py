from __future__ import annotations
from urllib.parse import urlparse, parse_qs, unquote

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
    if not href:
        return None, None
    href = href.strip()
    if href.startswith("/"):
        href = current_google_origin.rstrip("/") + href
    try:
        p = urlparse(href)
        qs = parse_qs(p.query)
        for key in ("imgrefurl", "url", "q"):
            vals = qs.get(key)
            if vals:
                candidate = unquote(vals[0])
                h = hostname(candidate)
                if h and not is_google_host(h):
                    img = None
                    if qs.get("imgurl"):
                        img = unquote(qs["imgurl"][0])
                    return candidate, img
        h = hostname(href)
        if h and not is_google_host(h):
            return href, None
    except Exception:
        return None, None
    return None, None
