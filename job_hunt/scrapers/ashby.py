from .ats_base import fetch_per_slug, strip_html
from .base import Scraper

URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"

# Verified-live Ashby boards.
SLUGS = [
    "vanta", "harvey", "posthog", "cohere", "lovable", "browserbase",
    "elevenlabs", "modal", "linear", "cursor", "openai",
]


def _location(j: dict) -> str | None:
    primary = j.get("location")
    secondary = j.get("secondaryLocations") or []
    bits = [primary] if primary else []
    if isinstance(secondary, list):
        bits.extend(s for s in secondary[:3] if isinstance(s, str))
    return ", ".join(bits) or None


class AshbyScraper(Scraper):
    name = "ashby"
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
                    "url": j.get("jobUrl"),
                    "description": (j.get("descriptionPlain") or "").strip()
                    or strip_html(j.get("descriptionHtml")),
                    "posted_at": j.get("publishedAt") or j.get("updatedAt"),
                }
                for j in jobs
            ]

        return fetch_per_slug(URL, SLUGS, parse)
