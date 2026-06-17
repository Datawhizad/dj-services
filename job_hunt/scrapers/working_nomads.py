import re
from html import unescape

import httpx

from .base import Scraper

API_URL = "https://www.workingnomads.com/jobsapi/_search"
TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str | None) -> str | None:
    if not html:
        return None
    return unescape(TAG_RE.sub("", html)).strip() or None


def _location(j: dict) -> str | None:
    base = (j.get("location_base") or "").strip()
    if base:
        return base
    locs = j.get("locations") or []
    if isinstance(locs, list) and locs:
        return ", ".join(str(x) for x in locs[:3])
    return None


class WorkingNomadsScraper(Scraper):
    name = "working_nomads"

    def fetch(self, query: str) -> list[dict]:
        # Quoted phrase boosts title matches; falls back to OR across description/tags.
        payload = {
            "size": 50,
            "from": 0,
            "query": {
                "query_string": {
                    "query": f'"{query}"',
                    "fields": ["title^3", "description", "tags"],
                }
            },
            "sort": [{"pub_date": "desc"}],
        }
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                API_URL,
                json=payload,
                headers={"Referer": "https://www.workingnomads.com/jobs"},
            )
            resp.raise_for_status()
            data = resp.json()

        out: list[dict] = []
        for hit in data.get("hits", {}).get("hits", []):
            j = hit.get("_source", {}) or {}
            jid = str(j.get("id") or hit.get("_id") or "")
            apply_url = j.get("apply_url") or f"https://www.workingnomads.com/jobs/{j.get('slug', '')}"
            out.append(
                {
                    "source": self.name,
                    "source_job_id": jid,
                    "title": (j.get("title") or "").strip(),
                    "company": j.get("company"),
                    "location": _location(j),
                    "url": apply_url,
                    "description": _strip_html(j.get("description")),
                    "posted_at": j.get("pub_date"),
                }
            )
        return out
