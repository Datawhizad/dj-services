import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "jobs.db"

TABLES = """
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
    match_score INTEGER,
    match_reason TEXT,
    UNIQUE(source, source_job_id)
);

CREATE TABLE IF NOT EXISTS applications (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'not_applied',
    notes TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_versions (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    pdf_path TEXT,
    json_path TEXT,
    summary TEXT,
    generated_at TEXT NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_jobs_scraped_at ON jobs(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score DESC);
"""

VALID_STATUSES = {"not_applied", "applied", "interview", "rejected", "offer"}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(TABLES)
        # migrate existing DBs that pre-date milestone 2
        _add_column_if_missing(conn, "jobs", "match_score", "match_score INTEGER")
        _add_column_if_missing(conn, "jobs", "match_reason", "match_reason TEXT")
        conn.executescript(INDEXES)


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
    # Sort: scored jobs first (highest score), then unscored by recency.
    return conn.execute(
        """SELECT j.id, j.source, j.title, j.company, j.location, j.url,
                  j.posted_at, j.scraped_at, j.match_score, j.match_reason,
                  a.status, r.pdf_path AS resume_pdf
           FROM jobs j
           LEFT JOIN applications a ON a.job_id = j.id
           LEFT JOIN resume_versions r ON r.job_id = j.id
           ORDER BY (j.match_score IS NULL), j.match_score DESC, j.scraped_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()


def get_job_for_tailoring(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, title, company, location, description, url FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()


def save_resume_version(
    conn: sqlite3.Connection, job_id: int, pdf_path: str, json_path: str, summary: str
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO resume_versions (job_id, pdf_path, json_path, summary, generated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (job_id, pdf_path, json_path, summary, datetime.utcnow().isoformat(timespec="seconds")),
    )


def get_unscored_jobs(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT id, title, company, location, description
           FROM jobs
           WHERE match_score IS NULL
           ORDER BY scraped_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()


def set_match_score(conn: sqlite3.Connection, job_id: int, score: int, reason: str) -> None:
    conn.execute(
        "UPDATE jobs SET match_score = ?, match_reason = ? WHERE id = ?",
        (score, reason, job_id),
    )


def prune_stale_unmatched(conn: sqlite3.Connection, title_predicate) -> int:
    """Delete jobs the user has not engaged with whose title no longer matches.
    Only removes rows where match_score IS NULL AND application status = 'not_applied'.
    Returns count deleted."""
    rows = conn.execute(
        """SELECT j.id, j.title FROM jobs j
           LEFT JOIN applications a ON a.job_id = j.id
           WHERE j.match_score IS NULL
             AND (a.status IS NULL OR a.status = 'not_applied')"""
    ).fetchall()
    to_delete = [r["id"] for r in rows if not title_predicate(r["title"])]
    if to_delete:
        placeholders = ",".join("?" for _ in to_delete)
        conn.execute(f"DELETE FROM jobs WHERE id IN ({placeholders})", to_delete)
    return len(to_delete)


def update_status(conn: sqlite3.Connection, job_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    conn.execute(
        "UPDATE applications SET status = ?, updated_at = ? WHERE job_id = ?",
        (status, datetime.utcnow().isoformat(timespec="seconds"), job_id),
    )
