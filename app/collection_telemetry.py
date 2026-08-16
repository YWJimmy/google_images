from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
import uuid


SCHEMA = """
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    chrome_id TEXT,
    session_mode TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    requested_count INTEGER NOT NULL,
    start_index INTEGER NOT NULL DEFAULT 1,
    configured_delay_seconds REAL,
    status TEXT NOT NULL,
    search_attempt_count INTEGER NOT NULL DEFAULT 0,
    completed_count INTEGER NOT NULL DEFAULT 0,
    challenge_count INTEGER NOT NULL DEFAULT 0,
    first_challenge_index INTEGER
);

CREATE TABLE IF NOT EXISTS collection_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    task_index INTEGER NOT NULL,
    search_sequence_number INTEGER NOT NULL,
    task_attempt_number INTEGER NOT NULL,
    completed_before INTEGER NOT NULL,
    actual_start_gap_ms INTEGER,
    configured_delay_seconds REAL,
    chrome_id TEXT,
    verification_ordinal INTEGER,
    profile_name TEXT,
    masked_ip TEXT,
    proxy_status TEXT,
    ip_event_status TEXT,
    proxy_latency_ms INTEGER,
    egress_checked_at TEXT,
    clash_group TEXT,
    clash_node TEXT,
    FOREIGN KEY(run_id) REFERENCES collection_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_collection_runs_started_at
ON collection_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_collection_events_occurred_at
ON collection_events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_collection_events_run_id
ON collection_events(run_id);
"""

EVENT_MIGRATIONS = {
    "verification_ordinal": "INTEGER",
    "profile_name": "TEXT",
    "masked_ip": "TEXT",
    "proxy_status": "TEXT",
    "ip_event_status": "TEXT",
    "proxy_latency_ms": "INTEGER",
    "egress_checked_at": "TEXT",
    "clash_group": "TEXT",
    "clash_node": "TEXT",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class CollectionTelemetry:
    """Persist privacy-safe collection run and human-verification metadata."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.executescript(SCHEMA)
        existing = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(collection_events)")
        }
        for name, column_type in EVENT_MIGRATIONS.items():
            if name not in existing:
                try:
                    conn.execute(
                        f"ALTER TABLE collection_events ADD COLUMN {name} {column_type}"
                    )
                except sqlite3.OperationalError as exc:
                    if "duplicate column name" not in str(exc).lower():
                        raise
        return conn

    def start_run(
        self,
        *,
        mode: str,
        requested_count: int,
        start_index: int = 1,
        configured_delay_seconds: float | None = None,
        chrome_id: str | None = None,
        session_mode: str | None = None,
    ) -> str:
        run_id = uuid.uuid4().hex
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO collection_runs (
                    run_id, mode, chrome_id, session_mode, started_at,
                    requested_count, start_index, configured_delay_seconds, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')""",
                (
                    run_id,
                    mode,
                    chrome_id,
                    session_mode,
                    _now(),
                    int(requested_count),
                    int(start_index),
                    configured_delay_seconds,
                ),
            )
        return run_id

    def update_attempt_count(self, run_id: str, search_attempt_count: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE collection_runs SET search_attempt_count=? WHERE run_id=?",
                (int(search_attempt_count), run_id),
            )

    def record_human_verification(
        self,
        *,
        run_id: str,
        event_type: str,
        task_index: int,
        search_sequence_number: int,
        task_attempt_number: int,
        completed_before: int,
        actual_start_gap_ms: int | None,
        configured_delay_seconds: float | None,
        chrome_id: str | None = None,
        profile_name: str | None = None,
        masked_ip: str | None = None,
        proxy_status: str | None = None,
        ip_event_status: str = "verification_seen",
        proxy_latency_ms: int | None = None,
        egress_checked_at: str | None = None,
        clash_group: str | None = None,
        clash_node: str | None = None,
    ) -> int:
        if event_type not in {"challenge", "consent"}:
            raise ValueError("event_type must be challenge or consent")
        with closing(self._connect()) as conn, conn:
            verification_ordinal = int(
                conn.execute(
                    "SELECT COUNT(*) + 1 FROM collection_events WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0]
            )
            conn.execute(
                """INSERT INTO collection_events (
                    run_id, occurred_at, event_type, task_index,
                    search_sequence_number, task_attempt_number, completed_before,
                    actual_start_gap_ms, configured_delay_seconds, chrome_id,
                    verification_ordinal, profile_name, masked_ip, proxy_status,
                    ip_event_status, proxy_latency_ms, egress_checked_at,
                    clash_group, clash_node
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    _now(),
                    event_type,
                    int(task_index),
                    int(search_sequence_number),
                    int(task_attempt_number),
                    int(completed_before),
                    actual_start_gap_ms,
                    configured_delay_seconds,
                    chrome_id,
                    verification_ordinal,
                    profile_name,
                    masked_ip,
                    proxy_status,
                    ip_event_status,
                    proxy_latency_ms,
                    egress_checked_at,
                    clash_group,
                    clash_node,
                ),
            )
            conn.execute(
                """UPDATE collection_runs
                   SET challenge_count=challenge_count + 1,
                       first_challenge_index=COALESCE(first_challenge_index, ?),
                       search_attempt_count=MAX(search_attempt_count, ?)
                   WHERE run_id=?""",
                (int(task_index), int(search_sequence_number), run_id),
            )
        return verification_ordinal

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        completed_count: int,
        search_attempt_count: int,
    ) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """UPDATE collection_runs
                   SET finished_at=?, status=?, completed_count=?, search_attempt_count=?
                   WHERE run_id=?""",
                (
                    _now(),
                    status,
                    int(completed_count),
                    int(search_attempt_count),
                    run_id,
                ),
            )

    def recent_events(self, limit: int = 100) -> list[dict[str, object]]:
        with closing(self._connect()) as conn, conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT e.occurred_at, e.event_type, e.task_index,
                          e.search_sequence_number, e.task_attempt_number,
                          e.completed_before, e.actual_start_gap_ms,
                          e.configured_delay_seconds, e.chrome_id,
                          e.verification_ordinal, e.profile_name, e.masked_ip,
                          e.proxy_status, e.ip_event_status, e.proxy_latency_ms,
                          e.egress_checked_at, e.clash_group, e.clash_node,
                          r.run_id, r.mode, r.session_mode, r.started_at
                   FROM collection_events e
                   JOIN collection_runs r ON r.run_id=e.run_id
                   ORDER BY e.id DESC LIMIT ?""",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]


def telemetry_path(log_dir: Path) -> Path:
    return Path(log_dir) / "collection_telemetry.sqlite3"


def profile_network_snapshot(project_root: Path, cdp_endpoint: str) -> dict[str, object]:
    """Read the privacy-safe network metadata for the Profile behind a CDP endpoint."""
    try:
        port = urlsplit(cdp_endpoint).port
    except ValueError:
        port = None
    manifest = Path(project_root) / "private" / "dedicated_chrome_profiles.json"
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        entries = []
    entry = next(
        (
            item
            for item in entries
            if isinstance(item, dict) and str(item.get("port", "")) == str(port)
        ),
        None,
    )
    if not entry:
        return {
            "profile_name": None,
            "masked_ip": None,
            "proxy_status": "unconfigured",
            "proxy_latency_ms": None,
            "egress_checked_at": None,
        }
    return {
        "profile_name": str(entry.get("name", "")) or None,
        "masked_ip": entry.get("masked_ip"),
        "proxy_status": str(entry.get("proxy_status", "direct")),
        "proxy_latency_ms": entry.get("proxy_latency_ms"),
        "egress_checked_at": entry.get("proxy_checked_at"),
    }
