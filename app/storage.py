from __future__ import annotations
from pathlib import Path
import csv
import json
import sqlite3
from datetime import datetime

from .models import KeywordTask, SearchResult, ImageItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    keyword TEXT NOT NULL,
    target_domain TEXT NOT NULL,
    result_code INTEGER NOT NULL,
    result_type TEXT NOT NULL,
    matched_rank INTEGER,
    matched_url TEXT,
    collected_count INTEGER NOT NULL DEFAULT 0,
    google_url TEXT,
    elapsed_ms INTEGER,
    message TEXT,
    checked_at TEXT NOT NULL,
    UNIQUE(run_date, keyword, target_domain)
);
CREATE TABLE IF NOT EXISTS image_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER NOT NULL,
    rank INTEGER NOT NULL,
    page_url TEXT NOT NULL,
    image_url TEXT,
    FOREIGN KEY(result_id) REFERENCES results(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_results_run_date ON results(run_date);
"""

def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def load_tasks(path: Path, limit: int) -> list[KeywordTask]:
    tasks = []
    seen = set()
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not {"keyword", "target_domain"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV must contain keyword,target_domain")
        for row in reader:
            kw = (row.get("keyword") or "").strip()
            td = (row.get("target_domain") or "").strip()
            if not kw or not td:
                continue
            key = (kw.casefold(), td.casefold())
            if key in seen:
                continue
            seen.add(key)
            tasks.append(KeywordTask(kw, td))
            if len(tasks) >= limit:
                break
    return tasks


def already_done(conn, run_date: str, task: KeywordTask) -> bool:
    return conn.execute(
        "SELECT 1 FROM results WHERE run_date=? AND keyword=? AND target_domain=?",
        (run_date, task.keyword, task.target_domain),
    ).fetchone() is not None


def save_result(conn, run_date: str, result: SearchResult, items: list[ImageItem]) -> int:
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.execute("DELETE FROM results WHERE run_date=? AND keyword=? AND target_domain=?",
                 (run_date, result.keyword, result.target_domain))
    cur = conn.execute(
        """INSERT INTO results (
            run_date, keyword, target_domain, result_code, result_type,
            matched_rank, matched_url, collected_count, google_url, elapsed_ms,
            message, checked_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_date, result.keyword, result.target_domain, result.result_code, result.result_type,
         result.matched_rank, result.matched_url, result.collected_count, result.google_url,
         result.elapsed_ms, result.message, checked_at)
    )
    rid = cur.lastrowid
    conn.executemany(
        "INSERT INTO image_items (result_id, rank, page_url, image_url) VALUES (?, ?, ?, ?)",
        [(rid, x.rank, x.page_url, x.image_url) for x in items]
    )
    conn.commit()
    return rid


def export_csv(conn, run_date: str, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    cur = conn.execute(
        """SELECT run_date, keyword, target_domain, result_code, result_type,
                  matched_rank, matched_url, collected_count, google_url,
                  elapsed_ms, message, checked_at
           FROM results WHERE run_date=? ORDER BY id""", (run_date,))
    rows = cur.fetchall()
    headers = [d[0] for d in cur.description]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)
