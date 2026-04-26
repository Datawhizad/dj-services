from .ats_base import fetch_per_slug, strip_html
from .base import Scraper

URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

# Verified-live Greenhouse boards (as of 2026-04). Companies on this list all
# hire analysts/data folks. Edit freely — invalid slugs are silently skipped.
SLUGS = [
    "airbnb", "stripe", "figma", "vercel", "anthropic", "discord", "dropbox",
    "pinterest", "reddit", "twilio", "asana", "gitlab", "mercury", "brex",
    "coinbase", "robinhood", "chime", "datadog", "mongodb", "clickhouse",
    "launchdarkly", "grafanalabs", "planetscale",
]


def _location(j: dict) -> str | None:
    loc = (j.get("location") or {}).get("name")
    if loc:
        return loc
    offices = j.get("offices") or []
    if offices:
        return ", ".join(o.get("name", "") for o in offices[:3] if o.get("name"))
    return None


class GreenhouseScraper(Scraper):
    name = "greenhouse"
    bulk_mode = True

    def fetch(self, query: str) -> list[dict]:
        def parse(slug: str, data: dict) -> list[dict]:
            jobs = data.get("jobs") or []
            return [
                {
                    "source": self.name,
                    "source_job_id": f"{slug}-{j.get('id')}",
                    "title": (j.get("title") or "").strip(),
                    "company": slug,
                    "location": _location(j),
                    "url": j.get("absolute_url"),
                    "description": strip_html(j.get("content")),
                    "posted_at": j.get("updated_at"),
                }
                for j in jobs
            ]

        return fetch_per_slug(URL, SLUGS, parse)
