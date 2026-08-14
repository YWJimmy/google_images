from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class KeywordTask:
    keyword: str
    target_domain: str

@dataclass
class ImageItem:
    rank: int
    page_url: str
    image_url: Optional[str] = None

@dataclass
class SearchResult:
    keyword: str
    target_domain: str
    result_code: int
    result_type: str
    matched_rank: Optional[int] = None
    matched_url: Optional[str] = None
    collected_count: int = 0
    google_url: Optional[str] = None
    elapsed_ms: Optional[int] = None
    message: Optional[str] = None
