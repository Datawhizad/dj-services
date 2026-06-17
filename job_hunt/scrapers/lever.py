from .ats_base import fetch_per_slug, strip_html
from .base import Scraper

URL = "https://api.lever.co/v0/postings/{slug}?mode=json"

# Verified-live Lever boards (most JobFindEasy slugs have moved off Lever).
SLUGS = ["netflix", "spotify", "palantir", "kraken", "attentive"]


def _location(j: dict) -> str | None:
    cats = j.get("categories") or {}
    if cats.get("location"):
        return cats["location"]
    loc_list = j.get("allLocations") or []
    if isinstance(loc_list, list) and loc_list:
        return ", ".join(str(x) for x in loc_list[:3])
    return None


def _description(j: dict) -> str | None:
    plain = j.get("descriptionPlain")
    if plain:
        return plain.strip()
    parts = [strip_html(j.get("description") or "") or ""]
    for li in j.get("lists") or []:
        if li.get("text"):
            parts.append(strip_html(li["text"]))
        parts.append(strip_html(li.get("content")) or "")
    return "\n\n".join(p for p in parts if p) or None


class LeverScraper(Scraper):
    name = "lever"
    bulk_mode = True

    def fetch(self, query: str) -> list[dict]:
        def parse(slug: str, data) -> list[dict]:
            jobs = data if isinstance(data, list) else []
            return [
                {
                    "source": self.name,
                    "source_job_id": f"{slug}-{j.get('id')}",
                    "title": (j.get("text") or "").strip(),
                    "company": slug,
                    "location": _location(j),
                    "url": j.get("hostedUrl"),
                    "description": _description(j),
                    "posted_at": j.get("createdAt"),
                }
                for j in jobs
            ]

        return fetch_per_slug(URL, SLUGS, parse)
