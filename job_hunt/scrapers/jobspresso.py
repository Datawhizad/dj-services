"""Jobspresso has no public JSON; their /jobs/feed/ RSS endpoint returns ~50
recent listings with title, link, description, pubDate, and a 'Company<br>Location'
string in the dc:creator field."""

import re
from html import unescape

import feedparser

from .base import Scraper

FEED_URL = "https://jobspresso.co/jobs/feed/"
TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str | None) -> str | None:
    if not html:
        return None
    return unescape(TAG_RE.sub("", html)).strip() or None


def _split_creator(creator: str | None) -> tuple[str | None, str | None]:
    """Jobspresso encodes 'Company<br/>Location' in dc:creator."""
    if not creator:
        return None, None
    parts = re.split(r"<br\s*/?>|\n", creator, maxsplit=1)
    company = unescape(parts[0]).strip() or None
    location = unescape(parts[1]).strip() if len(parts) > 1 else None
    return company, location


class JobspressoScraper(Scraper):
    name = "jobspresso"
    bulk_mode = True

    def fetch(self, query: str) -> list[dict]:
        feed = feedparser.parse(FEED_URL)
        out: list[dict] = []
        for entry in feed.entries:
            company, location = _split_creator(entry.get("author") or entry.get("dc_creator"))
            link = entry.get("link") or ""
            jid = entry.get("id") or link
            out.append(
                {
                    "source": self.name,
                    "source_job_id": jid,
                    "title": (entry.get("title") or "").strip(),
                    "company": company,
                    "location": location,
                    "url": link,
                    "description": _strip_html(entry.get("summary")),
                    "posted_at": entry.get("published"),
                }
            )
        return out
