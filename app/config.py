from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml


def _as_bool(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise ValueError(f"{name} must be a boolean")

@dataclass(frozen=True)
class Config:
    daily_limit: int
    run_hours: float
    max_results: int
    base_url: str
    images_home_url: str
    search_navigation: str
    require_google_com_host: bool
    hl: str
    gl: str
    browser_channel: str
    headless: bool
    session_mode: str
    cdp_endpoint: str
    profile_dir: Path
    storage_state_path: Path
    persist_storage_state_updates: bool
    viewport_width: int
    viewport_height: int
    navigation_timeout_ms: int
    results_load_wait_ms: int
    results_poll_interval_ms: int
    search_parse_timeout_ms: int
    max_scroll_rounds: int
    scroll_pixels: int
    scroll_wait_ms: int
    include_subdomains: bool
    input_csv: Path
    database_path: Path
    output_dir: Path
    log_dir: Path

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
        images_home_url=str(raw.get("images_home_url", "https://images.google.com/ncr")),
        search_navigation=str(raw.get("search_navigation", "homepage")).strip().lower(),
        require_google_com_host=_as_bool(raw.get("require_google_com_host", True), "require_google_com_host"),
        hl=str(raw.get("hl", "en")),
        gl=str(raw.get("gl", "us")),
        browser_channel=str(raw.get("browser_channel", "chrome")),
        headless=_as_bool(raw.get("headless", False), "headless"),
        session_mode=str(raw.get("session_mode", "persistent_profile")).strip().lower(),
        cdp_endpoint=str(raw.get("cdp_endpoint", "http://127.0.0.1:9222")).strip(),
        profile_dir=(base / raw.get("profile_dir", "profile/google_profile")).resolve(),
        storage_state_path=(base / raw.get("storage_state_path", "private/google_state.json")).resolve(),
        persist_storage_state_updates=_as_bool(
            raw.get("persist_storage_state_updates", False), "persist_storage_state_updates"
        ),
        viewport_width=int(raw.get("viewport_width", 1440)),
        viewport_height=int(raw.get("viewport_height", 1000)),
        navigation_timeout_ms=int(raw.get("navigation_timeout_ms", 45000)),
        results_load_wait_ms=int(raw.get("results_load_wait_ms", 4000)),
        results_poll_interval_ms=int(raw.get("results_poll_interval_ms", 100)),
        search_parse_timeout_ms=int(raw.get("search_parse_timeout_ms", 5000)),
        max_scroll_rounds=int(raw.get("max_scroll_rounds", 12)),
        scroll_pixels=int(raw.get("scroll_pixels", 1800)),
        scroll_wait_ms=int(raw.get("scroll_wait_ms", 1200)),
        include_subdomains=_as_bool(raw.get("include_subdomains", True), "include_subdomains"),
        input_csv=(base / raw.get("input_csv", "keywords_1000.csv")).resolve(),
        database_path=(base / raw.get("database_path", "rank_tracker.sqlite3")).resolve(),
        output_dir=(base / raw.get("output_dir", "output")).resolve(),
        log_dir=(base / raw.get("log_dir", "logs")).resolve(),
    )
    if cfg.daily_limit <= 0 or cfg.run_hours <= 0:
        raise ValueError("daily_limit and run_hours must be > 0")
    if not (1 <= cfg.max_results <= 100):
        raise ValueError("max_results must be between 1 and 100 in v1")
    if cfg.session_mode not in {"persistent_profile", "storage_state", "manual_cdp"}:
        raise ValueError(
            "session_mode must be persistent_profile, storage_state, or manual_cdp"
        )
    if cfg.session_mode == "manual_cdp" and not cfg.cdp_endpoint:
        raise ValueError("cdp_endpoint is required for session_mode=manual_cdp")
    if cfg.search_navigation not in {"homepage", "direct"}:
        raise ValueError("search_navigation must be homepage or direct")
    if cfg.results_load_wait_ms <= 0 or cfg.results_poll_interval_ms <= 0:
        raise ValueError("result wait and poll intervals must be > 0")
    if cfg.search_parse_timeout_ms <= 0:
        raise ValueError("search_parse_timeout_ms must be > 0")
    if not cfg.input_csv.exists():
        raise ValueError(f"input_csv not found: {cfg.input_csv}")
    return cfg
