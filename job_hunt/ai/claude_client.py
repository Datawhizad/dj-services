import os
from functools import lru_cache
from pathlib import Path

from anthropic import Anthropic

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"

MASTER_RESUME_PATH = Path(__file__).resolve().parent.parent / "master_resume" / "anmol_master.md"


@lru_cache(maxsize=1)
def get_client() -> Anthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it in your shell before running scoring/generation."
        )
    return Anthropic()


@lru_cache(maxsize=1)
def get_master_resume() -> str:
    return MASTER_RESUME_PATH.read_text(encoding="utf-8")


def cached_resume_block() -> dict:
    """Return a content block with the master resume, marked for prompt caching.
    Reusing this across requests gives a cache hit and big cost savings."""
    return {
        "type": "text",
        "text": "<master_resume>\n" + get_master_resume() + "\n</master_resume>",
        "cache_control": {"type": "ephemeral"},
    }
