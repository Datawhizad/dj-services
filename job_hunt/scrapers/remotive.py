import re
from html import unescape

import httpx

from .base import Scraper

API_URL = "https://remotive.com/api/remote-jobs"
TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str | None) -> str | None:
    if not html:
        return None
    return unescape(TAG_RE.sub("", html)).strip() or None


class RemotiveScraper(Scraper):
    name = "remotive"

    def fetch(self, query: str) -> list[dict]:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(API_URL, params={"search": query})
            resp.raise_for_status()
            data = resp.json()

        out: list[dict] = []
        for j in data.get("jobs", []):
            out.append(
                {
                    "source": self.name,
                    "source_job_id": str(j["id"]),
                    "title": j.get("title", "").strip(),
                    "company": j.get("company_name"),
                    "location": j.get("candidate_required_location"),
                    "url": j.get("url"),
                    "description": _strip_html(j.get("description")),
                    "posted_at": j.get("publication_date"),
                }
            )
        return out
