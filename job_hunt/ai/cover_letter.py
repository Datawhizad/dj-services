import json
import re
from datetime import date

from .claude_client import SONNET, cached_resume_block, get_client

DESC_CHAR_LIMIT = 6000
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

SYSTEM_INSTRUCTIONS = """You write a tailored cover letter for a candidate based on their master resume and a specific job posting.

Output STRICT JSON matching this schema:
{
  "date": "string (e.g. 'April 25, 2026')",
  "recipient": "string (name if the JD mentions one, otherwise 'Hiring Manager')",
  "company": "string",
  "company_address": "string or null (only if JD includes a city/HQ)",
  "greeting": "string (e.g. 'Dear Hiring Manager,' or 'Dear Ms. Patel,')",
  "body_paragraphs": ["string", "string", "string"],
  "closing": "string (e.g. 'Sincerely,')",
  "signature_name": "string (the candidate's name)",
  "tailoring_notes": "string (one sentence — what you emphasized and why)"
}

Writing rules:
- 3 paragraphs, total 220-320 words. Concise.
- Paragraph 1: open by naming the role and company, and one specific reason this candidate is a fit (1-2 sentences). Avoid clichés like "I am writing to apply" or "I am excited to apply".
- Paragraph 2: 2-3 concrete examples from the master resume that map to the JD's stated needs. Use specific tools/numbers from the resume (e.g. "Power BI dashboards that cut reporting prep from 2 days to hours"). Do not invent metrics.
- Paragraph 3: brief close — what you'd bring, openness to next steps. Do not request a specific salary or interview format.
- First-person voice ("I"), professional but human tone. No corporate buzzwords ("synergize", "leverage best-in-class").
- DO NOT invent jobs, dates, employers, degrees, certifications, or quantified outcomes that aren't in the master resume.
- Do not repeat the resume verbatim — paraphrase and connect it to the JD.

Return JSON only. No prose before or after."""


def _build_user_text(job: dict) -> str:
    desc = (job.get("description") or "")[:DESC_CHAR_LIMIT]
    today = date.today().strftime("%B %-d, %Y")
    return (
        f"Today's date: {today}\n\n"
        f"<job>\n"
        f"Title: {job.get('title')}\n"
        f"Company: {job.get('company') or 'unknown'}\n"
        f"Location: {job.get('location') or 'unknown'}\n"
        f"Description:\n{desc}\n"
        f"</job>\n\n"
        "Write the candidate's cover letter for this role. "
        "Return JSON only — no markdown fences, no commentary."
    )


def write_cover_letter(job: dict) -> dict:
    """Return a structured cover-letter dict. Raises on API or parse failure."""
    client = get_client()
    resp = client.messages.create(
        model=SONNET,
        max_tokens=1500,
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
