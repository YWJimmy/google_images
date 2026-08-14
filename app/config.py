from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml

@dataclass(frozen=True)
class Config:
    daily_limit: int
    run_hours: float
    max_results: int
    base_url: str
    hl: str
    gl: str
    browser_channel: str
    headless: bool
    profile_dir: Path
    viewport_width: int
    viewport_height: int
    navigation_timeout_ms: int
    max_scroll_rounds: int
    scroll_pixels: int
    scroll_wait_ms: int
    require_full_depth: bool
    include_subdomains: bool
    input_csv: Path
    database_path: Path
    output_dir: Path
    log_dir: Path
    stop_on_challenge: bool

    @property
    def interval_seconds(self) -> float:
        return self.run_hours * 3600 / self.daily_limit


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    base = path.parent
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config(
        daily_limit=int(raw.get("daily_limit", 1000)),
        run_hours=float(raw.get("run_hours", 8)),
        max_results=int(raw.get("max_results", 100)),
        base_url=str(raw.get("base_url", "https://www.google.com/search")),
        hl=str(raw.get("hl", "en")),
        gl=str(raw.get("gl", "us")),
        browser_channel=str(raw.get("browser_channel", "chrome")),
        headless=bool(raw.get("headless", False)),
        profile_dir=(base / raw.get("profile_dir", "profile/google_profile")).resolve(),
        viewport_width=int(raw.get("viewport_width", 1440)),
        viewport_height=int(raw.get("viewport_height", 1000)),
        navigation_timeout_ms=int(raw.get("navigation_timeout_ms", 45000)),
        max_scroll_rounds=int(raw.get("max_scroll_rounds", 12)),
        scroll_pixels=int(raw.get("scroll_pixels", 1800)),
        scroll_wait_ms=int(raw.get("scroll_wait_ms", 1200)),
        require_full_depth=bool(raw.get("require_full_depth", True)),
        include_subdomains=bool(raw.get("include_subdomains", True)),
        input_csv=(base / raw.get("input_csv", "keywords_1000.csv")).resolve(),
        database_path=(base / raw.get("database_path", "rank_tracker.sqlite3")).resolve(),
        output_dir=(base / raw.get("output_dir", "output")).resolve(),
        log_dir=(base / raw.get("log_dir", "logs")).resolve(),
        stop_on_challenge=bool(raw.get("stop_on_challenge", True)),
    )
    if cfg.daily_limit <= 0 or cfg.run_hours <= 0:
        raise ValueError("daily_limit and run_hours must be > 0")
    if not (1 <= cfg.max_results <= 100):
        raise ValueError("max_results must be between 1 and 100 in v1")
    if not cfg.input_csv.exists():
        raise ValueError(f"input_csv not found: {cfg.input_csv}")
    return cfg
