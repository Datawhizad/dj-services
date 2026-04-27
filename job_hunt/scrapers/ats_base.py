"""Common helpers for ATS adapters (Greenhouse, Lever, Ashby, etc.).
Each ATS exposes a per-company-slug REST endpoint, so a single scraper
iterates a hardcoded slug list and accumulates results across them."""

import re
import time
from html import unescape
from typing import Iterable

import httpx

TAG_RE = re.compile(r"<[^>]+>")
POLITE_DELAY_S = 0.5  # space out per-slug calls so we don't hammer the ATS


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
    one stale slug doesn't break the whole refresh, and concatenate results.
    Sleeps POLITE_DELAY_S between calls."""
    out: list[dict] = []
    slug_list = list(slugs)
    with httpx.Client(timeout=timeout, headers=headers or {}) as client:
        for i, slug in enumerate(slug_list):
            try:
                resp = client.get(url_template.format(slug=slug))
                if resp.status_code != 200:
                    continue
                rows = parse_one(slug, resp.json())
                out.extend(rows)
            except Exception:
                # Stale slug or transient network — skip silently.
                pass
            if i < len(slug_list) - 1:
                time.sleep(POLITE_DELAY_S)
    return out
