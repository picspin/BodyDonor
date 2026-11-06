"""SQLite（默认）数据库初始化。PG 为可选 profile（docker-compose 只含 PG）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id     TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    source      TEXT,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS series (
    series_id           TEXT PRIMARY KEY,
    case_id             TEXT NOT NULL REFERENCES cases(case_id),
    modality            TEXT,
    num_slices          INTEGER,
    slice_thickness_mm  REAL,
    orientation         TEXT,
    hu_range            TEXT,
    storage_key         TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    case_id     TEXT,
    stage       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    payload     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT
);
"""


def connect(db_url: str) -> sqlite3.Connection:
    """db_url 形如 sqlite:///./bodydonor.db。仅支持 SQLite（PG 后置）。"""
    if not db_url.startswith("sqlite:///"):
        raise ValueError(f"M0 仅支持 sqlite:///，收到: {db_url}")
    path = db_url.removeprefix("sqlite:///")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_url: str) -> None:
    conn = connect(db_url)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
