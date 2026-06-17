import re
from html import unescape

import httpx

from .base import Scraper

API_URL = "https://remoteok.com/api"
TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str | None) -> str | None:
    if not html:
        return None
    return unescape(TAG_RE.sub("", html)).strip() or None


def _tag_param(query: str) -> str:
    """Convert 'Data Analyst' -> 'analyst' (RemoteOK tags are short single words)."""
    q = query.lower()
    for kw in ("analyst", "analytics", "data scientist", "intelligence", "intern"):
        if kw in q:
            return kw.replace(" ", "+")
    return q.split()[-1]  # last word as fallback


class RemoteOKScraper(Scraper):
    name = "remoteok"

    def fetch(self, query: str) -> list[dict]:
        params = {"tags": _tag_param(query)}
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                API_URL,
                params=params,
                # RemoteOK requires a non-empty UA or returns 403
                headers={"User-Agent": "Mozilla/5.0 (compatible; anmol-jobhunt)"},
            )
            resp.raise_for_status()
            data = resp.json()

        # First element is API metadata, skip it
        out: list[dict] = []
        for j in data:
            if not isinstance(j, dict) or "id" not in j:
                continue
            out.append(
                {
                    "source": self.name,
                    "source_job_id": str(j["id"]),
                    "title": (j.get("position") or "").strip(),
                    "company": j.get("company"),
                    "location": j.get("location") or "Remote",
                    "url": j.get("url") or j.get("apply_url"),
                    "description": _strip_html(j.get("description")),
                    "posted_at": j.get("date"),
                }
            )
        return out
