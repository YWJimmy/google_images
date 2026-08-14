from __future__ import annotations

from dataclasses import dataclass
from html import unescape as html_unescape
import re
from urllib.parse import parse_qs, unquote, urlparse

from .ranking import hostname, is_google_host


SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)
UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
BRACKET_PAIRS = {")": "(", "]": "[", "}": "{"}


@dataclass(frozen=True)
class SourceDomainObservation:
    rank: int
    domains: tuple[str, ...]
    status: str
    method: str


def _goto_token(href: str) -> str:
    try:
        return parse_qs(urlparse(href).query).get("url", [""])[0]
    except Exception:
        return ""


def _decode_text(text: str) -> str:
    value = html_unescape(text).replace(r"\/", "/")
    value = UNICODE_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), value)
    for _ in range(2):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def extract_external_domains(text: str) -> tuple[str, ...]:
    """Extract normalized non-Google HTTP(S) domains from an in-memory block."""
    domains: set[str] = set()
    for match in URL_RE.findall(_decode_text(text)):
        url = match.rstrip(".,;:!?)]}")
        domain = hostname(url)
        if domain and not is_google_host(domain):
            domains.add(domain)
    return tuple(sorted(domains))


def _balanced_pairs(text: str) -> list[tuple[int, int]]:
    """Return balanced JS/JSON bracket ranges while ignoring quoted content."""
    pairs: list[tuple[int, int]] = []
    stack: list[tuple[str, int]] = []
    quote: str | None = None
    escaped = False
    for index, char in enumerate(text):
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char in "([{":
            stack.append((char, index))
        elif char in BRACKET_PAIRS:
            expected = BRACKET_PAIRS[char]
            if stack and stack[-1][0] == expected:
                _, start = stack.pop()
                pairs.append((start, index + 1))
            else:
                stack.clear()
    return pairs


def _smallest_domain_block(
    text: str, token: str, pairs: list[tuple[int, int]]
) -> tuple[int, tuple[str, ...]] | None:
    best: tuple[int, tuple[str, ...]] | None = None
    position = text.find(token)
    while position >= 0:
        containing = sorted(
            ((end - start, start, end) for start, end in pairs if start <= position < end),
            key=lambda item: item[0],
        )
        for length, start, end in containing:
            domains = extract_external_domains(text[start:end])
            if domains:
                if best is None or length < best[0]:
                    best = (length, domains)
                break
        position = text.find(token, position + len(token))
    return best


def _classify(rank: int, domains: tuple[str, ...], method: str) -> SourceDomainObservation:
    if len(domains) == 1:
        status = "resolved"
    elif domains:
        status = "ambiguous"
    else:
        status = "missing"
    return SourceDomainObservation(rank, domains, status, method)


def parse_minimal_structured_domains(
    page_html: str,
    candidate_hrefs: list[str],
    candidate_dom_blocks: list[list[str]] | None = None,
) -> list[SourceDomainObservation]:
    """Associate each Google result token with its smallest domain-bearing block.

    Tokens, blocks, and URLs are processed in memory only. Callers should persist
    only the returned normalized domains and statuses.
    """
    scripts = SCRIPT_RE.findall(page_html)
    pair_cache: dict[int, list[tuple[int, int]]] = {}
    candidate_dom_blocks = candidate_dom_blocks or [[] for _ in candidate_hrefs]
    observations: list[SourceDomainObservation] = []

    for index, href in enumerate(candidate_hrefs):
        rank = index + 1
        direct_domain = hostname(href)
        if direct_domain and not is_google_host(direct_domain):
            observations.append(_classify(rank, (direct_domain,), "direct_href"))
            continue
        token = _goto_token(href)
        script_matches: list[tuple[int, tuple[str, ...]]] = []
        if token:
            for script_index, script in enumerate(scripts):
                if token not in script:
                    continue
                pairs = pair_cache.setdefault(script_index, _balanced_pairs(script))
                match = _smallest_domain_block(script, token, pairs)
                if match:
                    script_matches.append(match)

        if script_matches:
            smallest_length = min(length for length, _ in script_matches)
            domains = tuple(sorted({
                domain
                for length, found in script_matches
                if length == smallest_length
                for domain in found
            }))
            observations.append(_classify(rank, domains, "structured_script"))
            continue

        dom_domains: tuple[str, ...] = ()
        blocks = candidate_dom_blocks[index] if index < len(candidate_dom_blocks) else []
        for block in blocks:
            found = extract_external_domains(block)
            if found:
                dom_domains = found
                break
        observations.append(
            _classify(rank, dom_domains, "dom_ancestor" if dom_domains else "none")
        )

    return observations
