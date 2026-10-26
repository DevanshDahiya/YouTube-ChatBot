# here we cache the video transacript in SQLite
import json
import os
import sqlite3
import time
from contextlib import contextmanager

CACHE_DB = os.path.join(os.getenv("DATA_DIR", "./data"), "cache.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id    TEXT PRIMARY KEY,
    chunk_count INTEGER NOT NULL,
    duration_s  REAL NOT NULL,
    transcript  TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS summaries (
    video_id   TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


@contextmanager
def _conn():
    os.makedirs(os.path.dirname(CACHE_DB) or ".", exist_ok=True)
    con = sqlite3.connect(CACHE_DB)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def get_video(video_id: str) -> dict | None:
    """Return the cached record (with transcript as a list), or None if not ingested."""
    with _conn() as con:
        row = con.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["transcript"] = json.loads(record["transcript"])
    return record


def save_video(video_id: str, snippets: list[dict], chunk_count: int) -> None:
    duration = snippets[-1]["start"] + snippets[-1]["duration"] if snippets else 0.0
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO videos (video_id, chunk_count, duration_s, transcript, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (video_id, chunk_count, duration, json.dumps(snippets), time.time()),
        )


def get_summary(video_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT summary FROM summaries WHERE video_id = ?", (video_id,)).fetchone()
    return json.loads(row["summary"]) if row else None


def save_summary(video_id: str, summary: dict) -> None:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO summaries (video_id, summary, created_at) VALUES (?, ?, ?)",
            (video_id, json.dumps(summary), time.time()),
        )
