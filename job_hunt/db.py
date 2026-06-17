import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .seniority import infer_seniority

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
    dedup_key TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    seniority TEXT,
    UNIQUE(source, source_job_id)
);

CREATE TABLE IF NOT EXISTS applications (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'not_applied',
    notes TEXT,
    applied_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resume_versions (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    pdf_path TEXT,
    json_path TEXT,
    summary TEXT,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cover_letters (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    pdf_path TEXT,
    json_path TEXT,
    summary TEXT,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    new_count INTEGER DEFAULT 0,
    seen_count INTEGER DEFAULT 0,
    filtered_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    error_first TEXT
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_jobs_scraped_at ON jobs(scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_dedup_key ON jobs(dedup_key);
CREATE INDEX IF NOT EXISTS idx_jobs_archived ON jobs(archived);
CREATE INDEX IF NOT EXISTS idx_jobs_seniority ON jobs(seniority);
CREATE INDEX IF NOT EXISTS idx_refresh_log_source_started ON refresh_log(source, started_at DESC);
"""

VALID_STATUSES = {"not_applied", "applied", "interview", "rejected", "offer"}
VALID_SENIORITIES = {"intern", "entry", "mid", "senior", "unknown"}

ARCHIVE_AFTER_DAYS = 30


# ---------- normalization for cross-source dedup ----------

_NORM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(s: str | None) -> str:
    if not s:
        return ""
    return _NORM_RE.sub("", s.lower()).strip()


def compute_dedup_key(company: str | None, title: str | None) -> str:
    """Stable dedup key across sources: sha1 of normalized (company|title)."""
    raw = f"{_normalize(company)}|{_normalize(title)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ---------- migrations / init ----------

def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(TABLES)
        # Migrate existing DBs that pre-date later milestones
        _add_column_if_missing(conn, "jobs", "match_score", "match_score INTEGER")
        _add_column_if_missing(conn, "jobs", "match_reason", "match_reason TEXT")
        _add_column_if_missing(conn, "jobs", "dedup_key", "dedup_key TEXT")
        _add_column_if_missing(conn, "jobs", "archived", "archived INTEGER NOT NULL DEFAULT 0")
        _add_column_if_missing(conn, "jobs", "seniority", "seniority TEXT")
        _add_column_if_missing(conn, "applications", "applied_at", "applied_at TEXT")
        # Backfill dedup_key for any rows missing it
        rows = conn.execute(
            "SELECT id, company, title FROM jobs WHERE dedup_key IS NULL OR dedup_key = ''"
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE jobs SET dedup_key = ? WHERE id = ?",
                (compute_dedup_key(r["company"], r["title"]), r["id"]),
            )
        # Backfill seniority for any rows missing it
        rows = conn.execute(
            "SELECT id, title, description FROM jobs WHERE seniority IS NULL OR seniority = ''"
        ).fetchall()
        for r in rows:
            conn.execute(
                "UPDATE jobs SET seniority = ? WHERE id = ?",
                (infer_seniority(r["title"], r["description"]), r["id"]),
            )
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


# ---------- jobs ----------

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

    dedup = compute_dedup_key(job.get("company"), job.get("title"))
    seniority = infer_seniority(job.get("title"), job.get("description"))
    cur = conn.execute(
        """INSERT INTO jobs (source, source_job_id, title, company, location, url,
                             description, posted_at, scraped_at, dedup_key, seniority)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            dedup,
            seniority,
        ),
    )
    job_id = cur.lastrowid
    conn.execute(
        "INSERT INTO applications (job_id, status, updated_at) VALUES (?, 'not_applied', ?)",
        (job_id, datetime.utcnow().isoformat(timespec="seconds")),
    )
    return job_id, True


# ---------- location classification ----------

LOCATION_REGEX = {
    "austin": re.compile(r"(?i)\b(austin|atx)\b"),
    "remote": re.compile(r"(?i)\bremote\b|\banywhere\b|worldwide|work from home"),
    "us": re.compile(r"(?i)\b(USA|U\.S\.A?\.|United States|US-only|US Remote|in the US)\b|, ?[A-Z]{2}\b"),
}


def matches_location(loc: str | None, kind: str) -> bool:
    if not loc:
        return kind == "anywhere"
    if kind == "anywhere":
        return True
    pat = LOCATION_REGEX.get(kind)
    return bool(pat and pat.search(loc))


# ---------- list / filter ----------

def list_jobs(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    source: str | None = None,
    min_score: int | None = None,
    query: str | None = None,
    location: str | None = None,
    seniority: list[str] | None = None,
    include_archived: bool = False,
    deduped: bool = True,
    limit: int = 200,
) -> list[sqlite3.Row]:
    """Return job rows matching the given filters.

    deduped=True (default) collapses cross-source duplicates by dedup_key,
    keeping the row with the highest match_score (or most recent scrape if
    none scored). The 'also_on' field counts the number of other source rows
    that share the same dedup_key, for the UI's 'also seen on N sources' badge.
    """
    where: list[str] = []
    args: list = []
    if status:
        where.append("COALESCE(a.status, 'not_applied') = ?")
        args.append(status)
    if source:
        where.append("j.source = ?")
        args.append(source)
    if min_score is not None:
        where.append("j.match_score >= ?")
        args.append(min_score)
    if query:
        where.append("(j.title LIKE ? OR j.company LIKE ?)")
        like = f"%{query}%"
        args.extend([like, like])
    if seniority:
        valid = [s for s in seniority if s in VALID_SENIORITIES]
        if valid:
            placeholders = ",".join("?" for _ in valid)
            where.append(f"COALESCE(j.seniority, 'unknown') IN ({placeholders})")
            args.extend(valid)
    if not include_archived:
        where.append("j.archived = 0")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    if deduped:
        # Window over dedup_key: keep the row with best (match_score desc, scraped_at desc)
        sql = f"""
            WITH ranked AS (
                SELECT j.*,
                       a.status, a.applied_at,
                       r.pdf_path AS resume_pdf,
                       c.pdf_path AS cover_letter_pdf,
                       ROW_NUMBER() OVER (
                           PARTITION BY j.dedup_key
                           ORDER BY (j.match_score IS NULL), j.match_score DESC, j.scraped_at DESC
                       ) AS rn,
                       COUNT(*) OVER (PARTITION BY j.dedup_key) AS dup_count
                  FROM jobs j
                  LEFT JOIN applications a ON a.job_id = j.id
                  LEFT JOIN resume_versions r ON r.job_id = j.id
                  LEFT JOIN cover_letters c ON c.job_id = j.id
                  {where_sql}
            )
            SELECT id, source, title, company, location, url, posted_at, scraped_at,
                   match_score, match_reason, status, applied_at, resume_pdf,
                   cover_letter_pdf, archived, seniority, (dup_count - 1) AS also_on
              FROM ranked
             WHERE rn = 1
             ORDER BY (match_score IS NULL), match_score DESC, scraped_at DESC
             LIMIT ?
        """
    else:
        sql = f"""
            SELECT j.id, j.source, j.title, j.company, j.location, j.url,
                   j.posted_at, j.scraped_at, j.match_score, j.match_reason,
                   a.status, a.applied_at,
                   r.pdf_path AS resume_pdf,
                   c.pdf_path AS cover_letter_pdf,
                   j.archived, j.seniority, 0 AS also_on
              FROM jobs j
              LEFT JOIN applications a ON a.job_id = j.id
              LEFT JOIN resume_versions r ON r.job_id = j.id
              LEFT JOIN cover_letters c ON c.job_id = j.id
              {where_sql}
             ORDER BY (j.match_score IS NULL), j.match_score DESC, j.scraped_at DESC
             LIMIT ?
        """
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    if location and location != "anywhere":
        rows = [r for r in rows if matches_location(r["location"], location)]
    return rows


def distinct_sources(conn: sqlite3.Connection) -> list[str]:
    return [
        r["source"]
        for r in conn.execute("SELECT DISTINCT source FROM jobs ORDER BY source").fetchall()
    ]


def get_job_for_tailoring(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, title, company, location, description, url FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()


def get_job_full(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT j.*, a.status, a.applied_at, a.notes
             FROM jobs j
             LEFT JOIN applications a ON a.job_id = j.id
            WHERE j.id = ?""",
        (job_id,),
    ).fetchone()


# ---------- artifacts ----------

def save_resume_version(
    conn: sqlite3.Connection, job_id: int, pdf_path: str, json_path: str, summary: str
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO resume_versions (job_id, pdf_path, json_path, summary, generated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (job_id, pdf_path, json_path, summary, datetime.utcnow().isoformat(timespec="seconds")),
    )


def save_cover_letter(
    conn: sqlite3.Connection, job_id: int, pdf_path: str, json_path: str, summary: str
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO cover_letters (job_id, pdf_path, json_path, summary, generated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (job_id, pdf_path, json_path, summary, datetime.utcnow().isoformat(timespec="seconds")),
    )


# ---------- scoring ----------

def get_unscored_jobs(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT id, title, company, location, description
             FROM jobs
            WHERE match_score IS NULL AND archived = 0
            ORDER BY scraped_at DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()


def set_match_score(conn: sqlite3.Connection, job_id: int, score: int, reason: str) -> None:
    conn.execute(
        "UPDATE jobs SET match_score = ?, match_reason = ? WHERE id = ?",
        (score, reason, job_id),
    )


# ---------- pruning + archival ----------

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


def auto_archive(conn: sqlite3.Connection) -> int:
    """Archive rows that are: rejected status, OR scraped >ARCHIVE_AFTER_DAYS ago and
    not engaged with (no application status change beyond not_applied, no resume, no
    score worth keeping). Returns count newly archived."""
    cutoff = (datetime.utcnow() - timedelta(days=ARCHIVE_AFTER_DAYS)).isoformat(timespec="seconds")
    cur = conn.execute(
        """UPDATE jobs SET archived = 1
            WHERE archived = 0
              AND id IN (
                  SELECT j.id FROM jobs j
                    LEFT JOIN applications a ON a.job_id = j.id
                    LEFT JOIN resume_versions r ON r.job_id = j.id
                   WHERE a.status = 'rejected'
                      OR (j.scraped_at < ?
                          AND COALESCE(a.status, 'not_applied') = 'not_applied'
                          AND r.job_id IS NULL)
              )""",
        (cutoff,),
    )
    return cur.rowcount or 0


def set_archived(conn: sqlite3.Connection, job_id: int, archived: bool) -> None:
    conn.execute("UPDATE jobs SET archived = ? WHERE id = ?", (1 if archived else 0, job_id))


# ---------- status ----------

def update_status(conn: sqlite3.Connection, job_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {status}")
    now = datetime.utcnow().isoformat(timespec="seconds")
    # Set applied_at on the first transition into 'applied'; preserve previous timestamp on later changes.
    if status == "applied":
        conn.execute(
            """UPDATE applications
                  SET status = ?,
                      updated_at = ?,
                      applied_at = COALESCE(applied_at, ?)
                WHERE job_id = ?""",
            (status, now, now, job_id),
        )
    else:
        conn.execute(
            "UPDATE applications SET status = ?, updated_at = ? WHERE job_id = ?",
            (status, now, job_id),
        )


# ---------- refresh log ----------

def start_refresh(conn: sqlite3.Connection, source: str) -> int:
    cur = conn.execute(
        "INSERT INTO refresh_log (source, started_at) VALUES (?, ?)",
        (source, datetime.utcnow().isoformat(timespec="seconds")),
    )
    return cur.lastrowid


def finish_refresh(
    conn: sqlite3.Connection,
    log_id: int,
    *,
    new_count: int,
    seen_count: int,
    filtered_count: int,
    error_count: int,
    error_first: str | None,
) -> None:
    conn.execute(
        """UPDATE refresh_log
              SET finished_at = ?, new_count = ?, seen_count = ?,
                  filtered_count = ?, error_count = ?, error_first = ?
            WHERE id = ?""",
        (
            datetime.utcnow().isoformat(timespec="seconds"),
            new_count,
            seen_count,
            filtered_count,
            error_count,
            error_first,
            log_id,
        ),
    )


def latest_per_source(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Most recent refresh entry per source, for the source-health panel."""
    return conn.execute(
        """SELECT source, started_at, finished_at, new_count, seen_count,
                  filtered_count, error_count, error_first
             FROM (
                 SELECT *, ROW_NUMBER() OVER (PARTITION BY source ORDER BY started_at DESC) AS rn
                   FROM refresh_log
             ) t
            WHERE rn = 1
            ORDER BY started_at DESC"""
    ).fetchall()
