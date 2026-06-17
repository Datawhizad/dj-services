import json
import re

from .claude_client import SONNET, cached_resume_block, get_client

DESC_CHAR_LIMIT = 6000
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_INSTRUCTIONS = """You tailor a candidate's master resume to a specific job posting.

Output STRICT JSON matching this schema:
{
  "name": "string",
  "contact_line": "string (single-line: phone, location, email, linkedin)",
  "summary": "string (2-3 sentences, tailored to the JD)",
  "skills": [
    {"category": "string", "items": ["string", ...]}
  ],
  "experience": [
    {
      "company": "string",
      "location": "string",
      "title": "string",
      "dates": "string",
      "bullets": ["string", ...]
    }
  ],
  "education": ["string", ...],
  "tailoring_notes": "string (one sentence — what you reframed and why)"
}

Tailoring rules — these are not negotiable:
- DO NOT invent jobs, dates, employers, degrees, certifications, or quantified outcomes that are not in the master resume. Fabricated experience is worse than a weak match.
- You MAY rewrite bullet wording, reorder bullets, reorder skill categories, and re-emphasize sections to match the JD's keywords and priorities.
- You MAY tighten the professional summary to mention skills/tools the JD specifically names IF they exist in the master resume.
- Keep the same companies, titles, and dates as the master resume.
- Skills lists may be re-ordered and skills the JD asks for can be moved to the front of their existing category — but do not add skills the candidate doesn't have.
- Aim for ~5-6 bullets per role, using strong verbs and quantified outcomes from the master resume.
- The tailoring_notes field should briefly explain what you adjusted (e.g. "Moved SQL and Power BI to top of skills, reframed KPMG bullets toward operational reporting metrics").

Return JSON only. No prose before or after."""


def _build_user_text(job: dict) -> str:
    desc = (job.get("description") or "")[:DESC_CHAR_LIMIT]
    return (
        f"<job>\n"
        f"Title: {job.get('title')}\n"
        f"Company: {job.get('company') or 'unknown'}\n"
        f"Location: {job.get('location') or 'unknown'}\n"
        f"Description:\n{desc}\n"
        f"</job>\n\n"
        "Tailor the master resume above (in the system block) for this job. "
        "Return JSON only — no markdown fences, no commentary."
    )


def tailor_resume(job: dict) -> dict:
    """Return a structured tailored resume dict. Raises on API or parse failure."""
    client = get_client()
    resp = client.messages.create(
        model=SONNET,
        max_tokens=2500,
        system=[
            {"type": "text", "text": SYSTEM_INSTRUCTIONS},
            cached_resume_block(),
        ],
        messages=[{"role": "user", "content": _build_user_text(job)}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
    match = JSON_RE.search(text)
    if not match:
        raise ValueError(f"could not find JSON in model output: {text[:200]}")
    return json.loads(match.group(0))
