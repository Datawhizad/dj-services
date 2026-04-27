"""Infer a job's seniority bucket from title + description.

Buckets:
  intern  — internships and co-ops
  entry   — explicitly entry/junior/associate/assistant/grad/trainee titles
            OR descriptions that say "no experience required" / "0-2 years"
  mid     — anything mentioning 2-4 years, no senior signal
  senior  — Senior/Staff/Principal/Lead/Manager/Director/Head/VP titles
            OR descriptions requiring 5+ years
  unknown — couldn't classify (rare)

Used to filter out senior-only postings for a junior-mid candidate hunt and
explicitly include internships when the user opts in.
"""

import re

# --- title heuristics ---
INTERN_RE = re.compile(r"(?i)\b(intern(?:ship)?|co[- ]?op)\b")
SENIOR_TITLE_RE = re.compile(
    r"(?i)\b(senior|sr\.?|staff|principal|lead|"
    r"director|head\s+of|vp\b|vice\s+president|"
    r"manager(?!\s+intern))\b"
)
ENTRY_TITLE_RE = re.compile(
    r"(?i)\b(entry[- ]?level|junior|jr\.?|associate|assistant|"
    r"trainee|graduate|new\s+grad|early[- ]?career|apprentice)\b"
)

# --- description heuristics ("X+ years") ---
# Matches "5+ years", "5 or more years", "minimum of 5 years", "at least 5 years",
# "5-7 years", "5 years of experience". Captures the lower-bound number.
YEARS_RE = re.compile(
    r"(?i)(?:minimum\s+of\s+|at\s+least\s+|over\s+)?"
    r"(\d{1,2})\s*\+?\s*"
    r"(?:to\s+\d{1,2}\s*|or\s+more\s+|-\s*\d{1,2}\s*)?"
    r"(?:years?|yrs?)"
    r"(?:\s+(?:of\s+)?(?:relevant\s+|professional\s+|hands[- ]on\s+|industry\s+|"
    r"work\s+|related\s+|applicable\s+)?(?:experience|exp\b))?"
)

NO_EXP_RE = re.compile(
    r"(?i)(no\s+(?:prior\s+)?experience\s+(?:required|necessary)|"
    r"\b0\s*[-+]?\s*\d?\s*years?\s+(?:of\s+)?experience|"
    r"entry[- ]?level\s+role)"
)

DESC_HEAD_LIMIT = 6000  # only inspect the first chunk of the description


def _max_years_in_text(text: str) -> int:
    """Return the highest 'N years of experience' lower-bound found, or 0 if none."""
    best = 0
    for m in YEARS_RE.finditer(text):
        try:
            n = int(m.group(1))
        except (TypeError, ValueError):
            continue
        # cap to avoid weird matches on "401k" or "2020 years"
        if 0 <= n <= 25 and n > best:
            best = n
    return best


def infer_seniority(title: str | None, description: str | None) -> str:
    t = (title or "").strip()
    d = (description or "")[:DESC_HEAD_LIMIT]

    # Internships first — explicit and common.
    if INTERN_RE.search(t):
        return "intern"
    # Title-based senior signal trumps description (covers cases where the JD
    # mentions "preferred 3+ years" but the title is "Senior X").
    if SENIOR_TITLE_RE.search(t):
        return "senior"
    if ENTRY_TITLE_RE.search(t) or NO_EXP_RE.search(d):
        return "entry"

    # Description-based years bucket.
    yrs = _max_years_in_text(d)
    if yrs >= 5:
        return "senior"
    if yrs >= 2:
        return "mid"
    if yrs == 1 or NO_EXP_RE.search(d):
        return "entry"

    # Some titles imply mid even without explicit signals (e.g. "Data Analyst" alone).
    if t:
        return "mid"
    return "unknown"
