"""SmartRecruiters per-company adapter. List endpoint gives metadata; description
sections live in a separate detail call. We cap the per-slug detail fetches to
keep refresh time bounded."""

import time

import httpx

from .ats_base import strip_html
from .base import Scraper

LIST_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}"
SLUGS = ["visa", "uber"]
MAX_DETAIL_PER_SLUG = 50
DETAIL_DELAY_S = 0.3  # be polite


def _location(loc: dict | None) -> str | None:
    if not loc:
        return None
    full = (loc.get("fullLocation") or "").strip()
    if full:
        return full
    parts = [loc.get(k) for k in ("city", "region", "country") if loc.get(k)]
    return ", ".join(p for p in parts if p) or None


def _description(detail: dict) -> str | None:
    sections = (detail.get("jobAd") or {}).get("sections") or {}
    chunks: list[str] = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        s = sections.get(key)
        if isinstance(s, dict):
            text = strip_html(s.get("text"))
            if text:
                title = (s.get("title") or "").strip()
                chunks.append(f"{title}\n{text}" if title else text)
    return "\n\n".join(chunks) or None


class SmartRecruitersScraper(Scraper):
    name = "smartrecruiters"
    bulk_mode = True

    def fetch(self, query: str) -> list[dict]:
        out: list[dict] = []
        with httpx.Client(timeout=20.0) as client:
            for slug in SLUGS:
                try:
                    resp = client.get(LIST_URL.format(slug=slug), params={"limit": 100})
                    if resp.status_code != 200:
                        continue
                    postings = resp.json().get("content") or []
                except Exception:
                    continue

                for j in postings[:MAX_DETAIL_PER_SLUG]:
                    posting_id = j.get("id")
                    if not posting_id:
                        continue
                    try:
                        d = client.get(DETAIL_URL.format(slug=slug, posting_id=posting_id))
                        detail = d.json() if d.status_code == 200 else {}
                    except Exception:
                        detail = {}
                    out.append(
                        {
                            "source": self.name,
                            "source_job_id": f"{slug}-{posting_id}",
                            "title": (j.get("name") or "").strip(),
                            "company": slug,
                            "location": _location(j.get("location")),
                            "url": detail.get("postingUrl")
                            or detail.get("applyUrl")
                            or LIST_URL.format(slug=slug),
                            "description": _description(detail),
                            "posted_at": j.get("releasedDate"),
                        }
                    )
                    time.sleep(DETAIL_DELAY_S)
        return out
