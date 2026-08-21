"""SQLite persistence for the IP intelligence subsystem."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS node_table (
    node_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT,
    country TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ip_identity_table (
    full_ip TEXT PRIMARY KEY,
    country TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 0,
    failure INTEGER NOT NULL DEFAULT 0,
    challenge INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    cooling_until TEXT
);
CREATE TABLE IF NOT EXISTS node_ip_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node TEXT NOT NULL,
    full_ip TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY(full_ip) REFERENCES ip_identity_table(full_ip)
);
CREATE INDEX IF NOT EXISTS idx_node_ip_history_node
    ON node_ip_history(node, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_node_ip_history_ip
    ON node_ip_history(full_ip, observed_at DESC);
CREATE TABLE IF NOT EXISTS ip_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_ip TEXT,
    node TEXT,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_ip_event_ip
    ON ip_event(full_ip, occurred_at DESC);
CREATE TABLE IF NOT EXISTS rotation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rotation_id TEXT NOT NULL,
    attempt_id INTEGER NOT NULL,
    node TEXT,
    decision_node TEXT,
    full_ip TEXT,
    result TEXT NOT NULL,
    state TEXT NOT NULL,
    reason TEXT,
    occurred_at TEXT NOT NULL,
    details_json TEXT,
    UNIQUE(rotation_id, attempt_id)
);
CREATE INDEX IF NOT EXISTS idx_rotation_log_rotation
    ON rotation_log(rotation_id, attempt_id);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IpIntelligenceDatabase:
    """Small thread-safe repository with one SQLite connection per operation."""

    def __init__(self, path: str | Path = "private/ip_intelligence.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def upsert_node(self, name: str, node_type: str | None = None,
                    country: str | None = None) -> None:
        now = utc_now()
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO node_table(name, type, country, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       type=COALESCE(excluded.type, node_table.type),
                       country=COALESCE(excluded.country, node_table.country),
                       last_seen=excluded.last_seen""",
                (name, node_type, country, now, now),
            )

    def observe_ip(self, node: str, full_ip: str, *, country: str | None = None,
                   node_type: str | None = None, observed_at: str | None = None) -> None:
        timestamp = observed_at or utc_now()
        self.upsert_node(node, node_type, country)
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO ip_identity_table(full_ip, country, first_seen, last_seen)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(full_ip) DO UPDATE SET
                       country=COALESCE(excluded.country, ip_identity_table.country),
                       last_seen=excluded.last_seen""",
                (full_ip, country, timestamp, timestamp),
            )
            conn.execute(
                "INSERT INTO node_ip_history(node, full_ip, observed_at) VALUES (?, ?, ?)",
                (node, full_ip, timestamp),
            )

    def record_event(self, event_type: str, *, full_ip: str | None = None,
                     node: str | None = None, details: dict[str, Any] | None = None,
                     occurred_at: str | None = None) -> None:
        timestamp = occurred_at or utc_now()
        normalized = event_type.strip().lower()
        with self._lock, closing(self._connect()) as conn, conn:
            if full_ip:
                conn.execute(
                    """INSERT INTO ip_identity_table(full_ip, first_seen, last_seen)
                       VALUES (?, ?, ?)
                       ON CONFLICT(full_ip) DO UPDATE SET last_seen=excluded.last_seen""",
                    (full_ip, timestamp, timestamp),
                )
                column = {"success": "success", "challenge": "challenge"}.get(normalized)
                if column is None and normalized in {
                    "failure", "timeout", "switch_failed", "same_egress"
                }:
                    column = "failure"
                if column:
                    conn.execute(
                        f"UPDATE ip_identity_table SET {column}={column}+1 WHERE full_ip=?",
                        (full_ip,),
                    )
            conn.execute(
                """INSERT INTO ip_event(full_ip, node, event_type, occurred_at, details_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (full_ip, node, normalized, timestamp,
                 json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
            )

    def set_ip_state(self, full_ip: str, status: str,
                     cooling_until: str | None = None) -> None:
        normalized = status.upper()
        if normalized not in {"ACTIVE", "COOLING", "BLOCKED"}:
            raise ValueError("invalid IP state")
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE ip_identity_table SET status=?, cooling_until=? WHERE full_ip=?",
                (normalized, cooling_until, full_ip),
            )

    def latest_ip_for_node(self, node: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """SELECT h.full_ip, h.observed_at, i.country, i.first_seen,
                          i.last_seen, i.success, i.failure, i.challenge,
                          i.status, i.cooling_until
                   FROM node_ip_history h
                   JOIN ip_identity_table i ON i.full_ip=h.full_ip
                   WHERE h.node=? ORDER BY h.id DESC LIMIT 1""",
                (node,),
            ).fetchone()
        return dict(row) if row else None

    def nodes_for_ip(self, full_ip: str) -> list[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT node, MAX(id) AS latest FROM node_ip_history
                   WHERE full_ip=? GROUP BY node ORDER BY latest DESC""",
                (full_ip,),
            ).fetchall()
        return [str(row["node"]) for row in rows]

    def node_history(self, node: str, limit: int = 100) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT full_ip, observed_at FROM node_ip_history
                   WHERE node=? ORDER BY id DESC LIMIT ?""",
                (node, max(1, min(int(limit), 1000))),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_node_intelligence(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT n.name, n.type, n.country AS node_country,
                          h.full_ip, h.observed_at, i.country, i.first_seen, i.success,
                          i.failure, i.challenge, i.status, i.cooling_until,
                          (SELECT MAX(e.occurred_at) FROM ip_event e
                           WHERE e.full_ip=i.full_ip AND e.event_type='challenge')
                          AS last_challenge
                   FROM node_table n
                   LEFT JOIN node_ip_history h ON h.id=(
                       SELECT id FROM node_ip_history
                       WHERE node=n.name ORDER BY id DESC LIMIT 1
                   )
                   LEFT JOIN ip_identity_table i ON i.full_ip=h.full_ip
                   ORDER BY n.name"""
            ).fetchall()
        return [dict(row) for row in rows]

    def record_rotation(self, rotation_id: str, attempt_id: int, *, node: str | None,
                        decision_node: str | None, full_ip: str | None, result: str,
                        state: str, reason: str | None = None,
                        details: dict[str, Any] | None = None) -> None:
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO rotation_log(
                       rotation_id, attempt_id, node, decision_node, full_ip,
                       result, state, reason, occurred_at, details_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(rotation_id, attempt_id) DO UPDATE SET
                       node=excluded.node, decision_node=excluded.decision_node,
                       full_ip=excluded.full_ip, result=excluded.result,
                       state=excluded.state, reason=excluded.reason,
                       occurred_at=excluded.occurred_at,
                       details_json=excluded.details_json""",
                (rotation_id, attempt_id, node, decision_node, full_ip, result,
                 state, reason, utc_now(),
                 json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
            )
