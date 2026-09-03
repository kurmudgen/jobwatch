"""SQLite persistence. One row per matched posting, keyed on url."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from config import DB_FILE

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    url              TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    company          TEXT,
    title            TEXT,
    location         TEXT,
    remote           INTEGER,
    employment_type  TEXT,
    posted_at        TEXT,
    description_text TEXT,
    matched_keyword  TEXT,
    matched_in       TEXT,
    flags            TEXT,
    salary_min       REAL,
    salary_max       REAL,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    seen_count       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_postings_first_seen ON postings(first_seen);
CREATE INDEX IF NOT EXISTS idx_postings_company ON postings(company);
"""


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# Columns added after the table first shipped. Only ever ADD - a migration here
# must never drop or rewrite a column, so an older database keeps every row.
LATER_COLUMNS = (
    ("salary_min", "REAL"),
    ("salary_max", "REAL"),
)


def _migrate(conn: sqlite3.Connection) -> "list[str]":
    """Additively bring an existing database up to the current schema."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(postings)")}
    added = []
    for name, sqltype in LATER_COLUMNS:
        if name not in existing:
            conn.execute("ALTER TABLE postings ADD COLUMN " + name + " " + sqltype)
            added.append(name)
    if added:
        conn.commit()
    return added


def connect(path: "Path | None" = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_FILE))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def upsert_many(conn: sqlite3.Connection, postings: "list[dict]") -> "list[dict]":
    """Insert unseen postings, bump last_seen on ones we already had.

    Returns only the postings that were new to the database.
    """
    now = utcnow()
    new_rows = []
    seen_this_run = set()

    for posting in postings:
        url = (posting.get("url") or "").strip()
        if not url or url in seen_this_run:
            continue  # a posting can appear on two boards in one run
        seen_this_run.add(url)

        existing = conn.execute(
            "SELECT url FROM postings WHERE url = ?", (url,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE postings SET last_seen = ?, seen_count = seen_count + 1, "
                "title = ?, location = ?, employment_type = ?, flags = ? WHERE url = ?",
                (
                    now,
                    posting.get("title"),
                    posting.get("location"),
                    posting.get("employment_type"),
                    json.dumps(posting.get("flags") or []),
                    url,
                ),
            )
            continue

        conn.execute(
            "INSERT INTO postings (url, source, company, title, location, remote, "
            "employment_type, posted_at, description_text, matched_keyword, "
            "matched_in, flags, salary_min, salary_max, "
            "first_seen, last_seen, seen_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
            (
                url,
                posting.get("source"),
                posting.get("company"),
                posting.get("title"),
                posting.get("location"),
                1 if posting.get("remote") else 0,
                posting.get("employment_type"),
                posting.get("posted_at"),
                posting.get("description_text"),
                posting.get("matched_keyword"),
                posting.get("matched_in"),
                json.dumps(posting.get("flags") or []),
                posting.get("salary_min"),
                posting.get("salary_max"),
                now,
                now,
            ),
        )
        row = dict(posting)
        row["first_seen"] = now
        new_rows.append(row)

    conn.commit()
    return new_rows


def _row_to_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    try:
        data["flags"] = json.loads(data.get("flags") or "[]")
    except (ValueError, TypeError):
        data["flags"] = []
    data["remote"] = bool(data.get("remote"))
    return data


def recent(conn: sqlite3.Connection, days: int = 7) -> "list[dict]":
    cutoff = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    ).isoformat()
    rows = conn.execute(
        "SELECT * FROM postings WHERE first_seen >= ? "
        "ORDER BY company COLLATE NOCASE, first_seen DESC",
        (cutoff,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def counts(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM postings").fetchone()[0]
    return {"total": total}
