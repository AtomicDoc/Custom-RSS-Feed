import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY,
    url         TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    summary     TEXT,
    source      TEXT,
    weight      REAL DEFAULT 1.0,
    published   TEXT,
    fetched_at  TEXT NOT NULL,
    embedding   BLOB,
    story_id    INTEGER REFERENCES stories(id),
    aired       INTEGER DEFAULT 0          -- 1 once covered (or deliberately skipped) in an episode
);
CREATE TABLE IF NOT EXISTS stories (
    id           INTEGER PRIMARY KEY,
    headline     TEXT NOT NULL,
    summary      TEXT,                     -- what the listener has been told so far
    embedding    BLOB,                     -- running mean of member article embeddings
    n_articles   INTEGER DEFAULT 0,
    first_seen   TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    last_aired   TEXT,
    times_aired  INTEGER DEFAULT 0,
    -- state before the latest airing, so `run --force` can undo it and rebuild the same episode
    prev_headline   TEXT,
    prev_summary    TEXT,
    prev_last_aired TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
    id         INTEGER PRIMARY KEY,
    date       TEXT UNIQUE NOT NULL,
    title      TEXT NOT NULL,
    script     TEXT NOT NULL,
    show_notes TEXT,
    filename   TEXT NOT NULL,
    bytes      INTEGER,
    duration   INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_story ON articles(story_id);
CREATE INDEX IF NOT EXISTS idx_articles_aired ON articles(aired);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / "newsfeed.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# Columns added after the first release; CREATE TABLE IF NOT EXISTS won't add them to an existing database.
ADDED_COLUMNS = {"stories": {"prev_headline": "TEXT", "prev_summary": "TEXT", "prev_last_aired": "TEXT"}}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    conn.commit()


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
