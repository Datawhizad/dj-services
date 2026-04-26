import re
from html import unescape

import httpx

from .base import Scraper

API_URL = "https://himalayas.app/jobs/api"
TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str | None) -> str | None:
    if not html:
        return None
    return unescape(TAG_RE.sub("", html)).strip() or None


def _location(j: dict) -> str | None:
    locs = j.get("locationRestrictions") or []
    if isinstance(locs, list) and locs:
        return ", ".join(str(x) for x in locs[:3])
    tz = j.get("timezoneRestrictions") or []
    if isinstance(tz, list) and tz:
        return ", ".join(str(x) for x in tz[:3])
    return None


class HimalayasScraper(Scraper):
    name = "himalayas"

    def fetch(self, query: str) -> list[dict]:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(API_URL, params={"query": query, "size": 50})
            resp.raise_for_status()
            data = resp.json()

        out: list[dict] = []
        for j in data.get("jobs", []):
            slug = j.get("companySlug")
            jid = j.get("guid") or f"{slug}-{j.get('title')}"
            url = j.get("applicationLink") or (
                f"https://himalayas.app/companies/{slug}" if slug else None
            )
            out.append(
                {
                    "source": self.name,
                    "source_job_id": str(jid),
                    "title": (j.get("title") or "").strip(),
                    "company": j.get("companyName"),
                    "location": _location(j),
                    "url": url,
                    "description": _strip_html(j.get("description") or j.get("excerpt")),
                    "posted_at": j.get("pubDate"),
                }
            )
        return out
