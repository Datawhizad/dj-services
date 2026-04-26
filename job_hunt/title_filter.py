import re

# Match analyst / data / BI / insights / reporting / consulting / intern titles.
# Word boundaries avoid false positives like "Internal" or "International".
TITLE_RE = re.compile(
    r"\b("
    r"analyst|analytics?|insights?|"
    r"reporting|consulting|consultant|"
    r"intern|internship|"
    r"data\s+scien\w*|"
    r"business\s+intelligence|"
    r"decision\s+support|"
    r"\bbi\b|\bmis\b"
    r")\b",
    re.IGNORECASE,
)


def matches_target(title: str | None) -> bool:
    if not title:
        return False
    return bool(TITLE_RE.search(title))
