"""Hacker News monthly 'Ask HN: Who is hiring?' thread.

Each top-level comment is one company posting. The first line is conventionally:
  'Company | Location | Type | URL'
The rest of the comment is prose describing roles. We keep only comments whose
text mentions analyst/data/BI keywords, synthesize a title that pulls a relevant
role line if present, and stuff the full text into description for downstream
match-scoring.

Two API calls:
  1. Algolia /api/v1/search_by_date — find the most recent thread ID
  2. Firebase /v0/item/{id}.json — fetch thread + comments
"""

import re
from html import unescape

import httpx

from .base import Scraper

ALGOLIA_LATEST = (
    "https://hn.algolia.com/api/v1/search_by_date?"
    "tags=author_whoishiring,story&hitsPerPage=10"
)
FIREBASE_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")
ANALYST_HINT_RE = re.compile(
    r"(?i)\b(analyst|analytics?|data\s+scien\w*|business\s+intelligence|insights?|reporting|decision\s+support|\bbi\b|\bmis\b)"
)
ROLE_LINE_RE = re.compile(
    r"(?im)^\s*[-•*]?\s*([A-Z][^.\n]*\b(?:analyst|analytics|insights|"
    r"business intelligence|reporting|decision support|consulting)\b[^.\n]*)"
)


def _strip_html(html: str | None) -> str:
    if not html:
        return ""
    text = unescape(TAG_RE.sub("\n", html))
    return text.strip()


def _split_first_line(text: str) -> tuple[str | None, str | None, str | None]:
    """Parse 'Company | Location | Type | URL' first line. Missing pieces → None."""
    first = text.splitlines()[0] if text else ""
    parts = [p.strip() for p in first.split("|")]
    company = parts[0] if parts and parts[0] else None
    location = parts[1] if len(parts) > 1 and parts[1] else None
    work_type = parts[2] if len(parts) > 2 and parts[2] else None
    return company, location, work_type


def _extract_role_title(text: str, company: str | None) -> str:
    """Find an analyst-flavored role line in the body; fall back to a synthesized
    title if nothing matches but the keyword check passed."""
    m = ROLE_LINE_RE.search(text)
    if m:
        line = WHITESPACE_RE.sub(" ", m.group(1)).strip().rstrip(",;")
        return line[:120]
    return f"Analyst openings at {company or 'company'}"


class HNWhoIsHiringScraper(Scraper):
    name = "hn_who_is_hiring"
    bulk_mode = True

    def _latest_thread_id(self, client: httpx.Client) -> int | None:
        resp = client.get(ALGOLIA_LATEST)
        resp.raise_for_status()
        for hit in resp.json().get("hits", []):
            t = hit.get("title") or ""
            if t.startswith("Ask HN: Who is hiring?"):
                return int(hit["objectID"])
        return None

    def fetch(self, query: str) -> list[dict]:
        with httpx.Client(timeout=20.0) as client:
            thread_id = self._latest_thread_id(client)
            if not thread_id:
                return []
            thread_resp = client.get(FIREBASE_ITEM.format(id=thread_id))
            thread_resp.raise_for_status()
            thread = thread_resp.json() or {}
            kids = thread.get("kids") or []

            out: list[dict] = []
            for kid_id in kids:
                try:
                    r = client.get(FIREBASE_ITEM.format(id=kid_id))
                    if r.status_code != 200:
                        continue
                    c = r.json() or {}
                    if c.get("dead") or c.get("deleted"):
                        continue
                    text = _strip_html(c.get("text"))
                    if not ANALYST_HINT_RE.search(text):
                        continue
                    company, location, _ = _split_first_line(text)
                    title = _extract_role_title(text, company)
                    out.append(
                        {
                            "source": self.name,
                            "source_job_id": str(c.get("id") or kid_id),
                            "title": title,
                            "company": company,
                            "location": location,
                            "url": f"https://news.ycombinator.com/item?id={c.get('id', kid_id)}",
                            "description": text,
                            "posted_at": None,
                        }
                    )
                except Exception:
                    continue
            return out
