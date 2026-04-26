from abc import ABC, abstractmethod


class Scraper(ABC):
    """Each scraper yields normalized job dicts with these keys:
    source, source_job_id, title, company, location, url, description, posted_at."""

    name: str

    @abstractmethod
    def fetch(self, query: str) -> list[dict]:
        ...
