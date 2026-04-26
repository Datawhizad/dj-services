"""Common helpers for ATS adapters (Greenhouse, Lever, Ashby, etc.).
Each ATS exposes a per-company-slug REST endpoint, so a single scraper
iterates a hardcoded slug list and accumulates results across them."""

import re
from html import unescape
from typing import Iterable

import httpx

TAG_RE = re.compile(r"<[^>]+>")


def strip_html(html: str | None) -> str | None:
    if not html:
        return None
    return unescape(TAG_RE.sub("", html)).strip() or None


def fetch_per_slug(
    url_template: str,
    slugs: Iterable[str],
    parse_one: "callable",
    *,
    timeout: float = 15.0,
    headers: dict | None = None,
) -> list[dict]:
    """Hit url_template.format(slug=s) for each slug, swallow per-slug errors so
    one stale slug doesn't break the whole refresh, and concatenate results."""
    out: list[dict] = []
    with httpx.Client(timeout=timeout, headers=headers or {}) as client:
        for slug in slugs:
            try:
                resp = client.get(url_template.format(slug=slug))
                if resp.status_code != 200:
                    continue
                rows = parse_one(slug, resp.json())
                out.extend(rows)
            except Exception:
                # Stale slug or transient network — skip silently.
                continue
    return out
