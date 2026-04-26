import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    url TEXT NOT NULL,
    description TEXT,
    posted_at TEXT,
    scraped_at TEXT NOT NULL,
    UNIQUE(source, source_job_id)
);

CREATE TABLE IF NOT EXISTS applications (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'not_applied',
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_scraped_at ON jobs(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
"""

VALID_STATUSES = {"not_applied", "applied", "interview", "rejected", "offer"}


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_job(conn: sqlite3.Connection, job: dict) -> tuple[int, bool]:
    """Insert a job, or return existing id if (source, source_job_id) already exists.
    Returns (job_id, is_new)."""
    cur = conn.execute(
        "SELECT id FROM jobs WHERE source = ? AND source_job_id = ?",
        (job["source"], job["source_job_id"]),
    )
    row = cur.fetchone()
    if row:
        return row["id"], False

    cur = conn.execute(
        """INSERT INTO jobs (source, source_job_id, title, company, location, url,
                             description, posted_at, scraped_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job["source"],
            job["source_job_id"],
            job["title"],
            job.get("company"),
            job.get("location"),
            job["url"],
            job.get("description"),
            job.get("posted_at"),
            datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    job_id = cur.lastrowid
    conn.execute(
        "INSERT INTO applications (job_id, status, updated_at) VALUES (?, 'not_applied', ?)",
        (job_id, datetime.utcnow().isoformat(timespec="seconds")),
    )
    return job_id, True


def list_jobs(conn: sqlite3.Connection, limit: int = 200) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT j.id, j.source, j.title, j.company, j.location, j.url,
                  j.posted_at, j.scraped_at, a.status
           FROM jobs j
           LEFT JOIN applications a ON a.job_id = j.id
           ORDER BY j.scraped_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()


def update_status(conn: sqlite3.Connection, job_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    conn.execute(
        "UPDATE applications SET status = ?, updated_at = ? WHERE job_id = ?",
        (status, datetime.utcnow().isoformat(timespec="seconds"), job_id),
    )
