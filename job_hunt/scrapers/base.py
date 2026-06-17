from abc import ABC, abstractmethod


class Scraper(ABC):
    """Each scraper yields normalized job dicts with these keys:
    source, source_job_id, title, company, location, url, description, posted_at.

    bulk_mode = True for sources that don't support keyword search (RSS feeds,
    per-company ATS endpoints). The refresh loop calls fetch("") once for these
    instead of iterating TARGET_ROLES, so we don't hit the same endpoint 17 times
    per refresh."""

    name: str
    bulk_mode: bool = False

    @abstractmethod
    def fetch(self, query: str) -> list[dict]:
        ...
