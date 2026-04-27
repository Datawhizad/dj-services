"""WeWorkRemotely combined RSS feed. Title is encoded as 'Company : Role' and the
custom <region> field carries location."""

import re
from html import unescape

import feedparser
import httpx

from .base import Scraper

FEED_URL = "https://weworkremotely.com/remote-jobs.rss"
TAG_RE = re.compile(r"<[^>]+>")
UA = "Mozilla/5.0 (compatible; anmol-jobhunt)"


def _strip_html(html: str | None) -> str | None:
    if not html:
        return None
    return unescape(TAG_RE.sub("", html)).strip() or None


def _split_company_title(combined: str) -> tuple[str | None, str]:
    """WWR <title> has many formats: 'Company: Role', 'Company - Role',
    'Role at Company [job_id]', etc. Be conservative — only split if we find
    a clean delimiter, otherwise leave company unset and let the title be the
    whole string (better than mis-attributing)."""
    # 'Role at Company [12345]' style
    m = re.match(r"^(.*?)\s+at\s+(.+?)(?:\s*\[\d+\])?$", combined.strip(), re.IGNORECASE)
    if m and len(m.group(1)) >= 4 and len(m.group(2)) >= 2:
        return m.group(2).strip(), m.group(1).strip()
    # 'Company: Role' or 'Company - Role'
    for sep in (": ", " — ", " - "):
        if sep in combined:
            company, _, role = combined.partition(sep)
            company = company.strip()
            role = role.strip()
            # Sanity: company should be short-ish; if it looks like a sentence, abort split.
            if 1 <= len(company) <= 60 and role:
                return company or None, role
    return None, combined.strip()


class WeWorkRemotelyScraper(Scraper):
    name = "weworkremotely"
    bulk_mode = True

    def fetch(self, query: str) -> list[dict]:
        # WWR rejects feedparser's default UA — fetch ourselves with a browser UA.
        with httpx.Client(timeout=20.0, headers={"User-Agent": UA}, follow_redirects=True) as c:
            resp = c.get(FEED_URL)
            resp.raise_for_status()
            xml = resp.text

        feed = feedparser.parse(xml)
        out: list[dict] = []
        for entry in feed.entries:
            company, role = _split_company_title(entry.get("title") or "")
            link = entry.get("link") or ""
            jid = entry.get("id") or link
            location = entry.get("region") or entry.get("dc_creator") or None
            out.append(
                {
                    "source": self.name,
                    "source_job_id": jid,
                    "title": role,
                    "company": company,
                    "location": (location or "").strip() or None,
                    "url": link,
                    "description": _strip_html(entry.get("summary")),
                    "posted_at": entry.get("published"),
                }
            )
        return out
