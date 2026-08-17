"""Text and numeric parsing utilities shared across the pipeline."""
import re

from loguru import logger

_CURRENCY_SYMBOLS = ["₹", "Rs.", "Rs", "INR"]
_NUMBER_CLEAN_PATTERN = re.compile(r"[^\d.\-]")


def parse_indian_number(s: str | float | int | None) -> float | None:
    """Parse a monetary string into a float.

    Handles Indian numbering (lakh/crore comma grouping), currency symbols,
    parentheses for negatives, trailing dashes/em-dashes meaning nil, and
    stray whitespace. Returns None if the value cannot be parsed as a number.
    """
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)

    text = str(s).strip()
    if not text:
        return None

    # Common "nil" placeholders in Indian financial statements
    if text in {"-", "–", "—", "NIL", "Nil", "N/A", "NA", "n/a"}:
        return None

    for sym in _CURRENCY_SYMBOLS:
        text = text.replace(sym, "")
    text = text.strip()

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.startswith("-"):
        negative = True
        text = text[1:]

    text = text.replace(",", "").strip()

    # A genuine single Indian-formatted monetary figure never contains internal
    # whitespace. A malformed table cell holding several concatenated values
    # (e.g. "5,000 5,000 - - - - 536") does — reject it rather than silently
    # collapsing the spaces and concatenating unrelated digits into one number.
    if re.search(r"\s", text):
        logger.debug(f"Rejecting multi-token value (not a single number): {s!r}")
        return None

    cleaned = _NUMBER_CLEAN_PATTERN.sub("", text)
    if not cleaned or cleaned in {".", "-"}:
        return None
    if cleaned.count("-") > 1 or cleaned.count(".") > 1:
        logger.debug(f"Rejecting malformed numeric token: {s!r}")
        return None

    try:
        value = float(cleaned)
    except ValueError:
        logger.debug(f"Could not parse number from: {s!r}")
        return None

    return -value if negative else value


def format_indian_number(value: float | int, decimals: int = 0) -> str:
    """Format a number with Indian comma grouping (last 3 digits, then pairs) —
    the inverse of parse_indian_number's parsing, used when generating report
    text. E.g. 176020 -> '1,76,020', not the Western '176,020'."""
    negative = value < 0
    value = abs(value)
    if decimals:
        int_part, _, dec_part = f"{value:.{decimals}f}".partition(".")
    else:
        int_part, dec_part = str(int(round(value))), ""

    if len(int_part) > 3:
        last3, rest = int_part[-3:], int_part[:-3]
        groups = []
        while len(rest) > 2:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            groups.insert(0, rest)
        int_part = ",".join(groups) + "," + last3

    result = int_part + (f".{dec_part}" if dec_part else "")
    return f"-{result}" if negative else result


def clean_label(label: str) -> str:
    """Lowercase and strip a raw line-item label for matching.

    Strips trailing colons, bracketed content (note refs like "(3)"), and
    collapses whitespace.
    """
    if not label:
        return ""
    text = label.strip().lower()
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = text.rstrip(":").strip()
    text = re.sub(r"\s+", " ", text)
    return text


PERIOD_PATTERN = re.compile(
    r"(?:year|period)\s+ended?\s+(\d{1,2}\s+\w+\s+\d{4})", re.IGNORECASE
)


def detect_company_name(text: str) -> str | None:
    """Longest all-caps line (>3 words) before the word LIMITED/LTD.

    Shared across the PDF, DOCX, and image extractors so company-name
    detection behaves identically regardless of source document format.
    """
    candidates = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ("LIMITED" in line.upper() or "LTD" in line.upper()) and line.isupper():
            words = line.split()
            if len(words) > 3:
                candidates.append(line)
    if not candidates:
        return None
    return max(candidates, key=len)


def detect_period(text: str) -> str | None:
    match = PERIOD_PATTERN.search(text)
    return match.group(1) if match else None


def extract_note_ref(label: str) -> str | None:
    """Extract a note number/letter reference from a raw label, if present."""
    if not label:
        return None
    match = re.search(r"\bnote\s*(?:no\.?)?\s*(\d+[a-zA-Z]?)\b", label, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"\((\d+[a-zA-Z]?)\)\s*$", label.strip())
    if match:
        return match.group(1)
    return None
