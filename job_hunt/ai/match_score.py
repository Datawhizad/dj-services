import json
import re

from .claude_client import HAIKU, cached_resume_block, get_client

DESC_CHAR_LIMIT = 4000

SYSTEM_INSTRUCTIONS = """You score how well a candidate's master resume matches a job posting.

Output a single JSON object: {"score": <int 0-100>, "reason": "<one short sentence>"}.

Scoring rubric:
- 85-100: strong match — title, seniority, and core skills all align.
- 65-84: good match — most skills align, minor gaps in seniority or domain.
- 45-64: partial match — relevant background but meaningful gaps.
- 25-44: weak match — only a few transferable skills.
- 0-24: not a match — wrong field, wrong seniority, or unrelated.

Be honest. Do not inflate scores. The reason must cite the most important factor (e.g. "Title matches and Power BI/SQL stack lines up", or "Senior role; resume shows ~14 months experience")."""

JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _build_user_text(job: dict) -> str:
    desc = (job.get("description") or "")[:DESC_CHAR_LIMIT]
    return (
        f"<job>\n"
        f"Title: {job.get('title')}\n"
        f"Company: {job.get('company') or 'unknown'}\n"
        f"Location: {job.get('location') or 'unknown'}\n"
        f"Description:\n{desc}\n"
        f"</job>\n\n"
        "Score this job for the candidate in <master_resume>. "
        'Reply with JSON only: {"score": int, "reason": "..."}.'
    )


def score_job(job: dict) -> tuple[int, str]:
    """Return (score 0-100, one-line reason). Raises on API failure."""
    client = get_client()
    resp = client.messages.create(
        model=HAIKU,
        max_tokens=200,
        system=[
            {"type": "text", "text": SYSTEM_INSTRUCTIONS},
            cached_resume_block(),
        ],
        messages=[{"role": "user", "content": _build_user_text(job)}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

    match = JSON_RE.search(text)
    if not match:
        return 0, f"could not parse model output: {text[:120]}"
    try:
        data = json.loads(match.group(0))
        score = int(data.get("score", 0))
        score = max(0, min(100, score))
        reason = str(data.get("reason", "")).strip()[:300]
        return score, reason
    except (ValueError, json.JSONDecodeError) as e:
        return 0, f"parse error: {e}"
