"""Best-effort canonicalization of statutory citations.

The extraction worker is prompted to emit canonical citations already, but
reconciliation benefits from a shared, deterministic normalizer so that
"26 U.S.C. 6511", "IRC 6511(a)", and "section 6511(a)" can be compared as the
same reference. These helpers are intentionally conservative: if a citation
cannot be confidently normalized, it is returned largely unchanged rather than
guessed.
"""

from __future__ import annotations

import re

IRC_SECTION_RE = re.compile(
    r"\b(?:section|sec\.?|\u00a7)\s*(?P<num>\d+[A-Za-z]?)"
    r"(?P<path>(?:\(\s*[0-9A-Za-z]+\s*\))*)",
    re.IGNORECASE,
)
USC_RE = re.compile(
    r"\b(?P<title>\d{1,3})\s*U\.?S\.?C\.?\s*(?:section|sec\.?|\u00a7)?\s*"
    r"(?P<num>\d+[A-Za-z]?)"
    r"(?P<path>(?:\(\s*[0-9A-Za-z]+\s*\))*)",
)
_IRC_TITLE = "26"


def _clean_path(path: str | None) -> str:
    if not path:
        return ""
    parts = re.findall(r"\(\s*([0-9A-Za-z]+)\s*\)", path)
    if not parts:
        return ""
    return "(" + ")(".join(parts) + ")"


def normalize_irc(num: str | int, path: str | None = None) -> str:
    """Build the canonical 'IRC § <num><path>' form."""
    num = str(num)
    return f"IRC § {num}{_clean_path(path)}"


def normalize_usc(title: str | int, num: str | int, path: str | None = None) -> str:
    """Build the canonical '<title> U.S.C. § <num><path>' form."""
    return f"{int(title)} U.S.C. § {num}{_clean_path(path)}"


def _strip_whitespace_in_path(path: str) -> str:
    return re.sub(r"\s+", "", path or "")


def classify(raw: str) -> str:
    """Return 'irc', 'usc', or 'unknown' for a citation string."""
    if USC_RE.search(raw) and _IRC_TITLE in USC_RE.search(raw).group("title"):
        return "irc"
    if USC_RE.search(raw):
        return "usc"
    low = raw.lower()
    if "internal revenue code" in low or "irc" in low or "code of 1986" in low:
        return "irc"
    if IRC_SECTION_RE.search(raw):
        # A bare "section N" may or may not be IRC; treat as ambiguous.
        return "unknown"
    return "unknown"


def normalize_citation(raw: str) -> str:
    """Normalize a citation to its canonical form where possible.

    IRC references become ``IRC § <num><path>``. Other-code references become
    ``<title> U.S.C. § <num><path>``. Unrecognized input is returned stripped
    of surrounding whitespace.
    """
    text = re.sub(r"\s+", " ", raw.strip())
    if not text:
        return text

    m = USC_RE.search(text)
    if m:
        title = m.group("title")
        path = _strip_whitespace_in_path(m.group("path"))
        if title == _IRC_TITLE:
            return normalize_irc(m.group("num"), path)
        return normalize_usc(title, m.group("num"), path)

    low = text.lower()
    if "internal revenue code" in low or "irc" in low or "code of 1986" in low:
        m2 = IRC_SECTION_RE.search(text)
        if m2:
            path = _strip_whitespace_in_path(m2.group("path"))
            return normalize_irc(m2.group("num"), path)
        # IRC named but no parseable number.
        return f"IRC § {text}"

    m3 = IRC_SECTION_RE.search(text)
    if m3:
        # Bare "section N"; caller context decides whether it is IRC. Keep the
        # number but do not assert a code.
        path = _strip_whitespace_in_path(m3.group("path"))
        return f"§ {m3.group('num')}{path}"

    return text
