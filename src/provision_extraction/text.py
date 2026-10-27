"""Convert a raw text string into stable, numbered display lines.

The prepared lines are the canonical reference frame for the whole run. Every
location used by the tools, the worker, and reconciliation is a 1-based line
number into this list. The preparation is deterministic so that repeated runs
over the same input text produce identical lines.

Design notes
------------
- Existing newlines are preserved; a source line longer than ``max_chars`` is
  wrapped at word boundaries.
- Page decorations (e.g. ``[Page 3]``) are ordinary lines, so they remain
  part of the reference frame and can be cited like anything else.
- Blank lines are kept single so the text stays readable without ballooning.
"""

from __future__ import annotations


def prepare_lines(text: str, max_chars: int = 120) -> list[str]:
    """Split ``text`` into stable display lines.

    Long lines are wrapped at word boundaries. Consecutive blank lines are
    collapsed to one. Leading/trailing blank lines are dropped. The result is
    deterministic for a given ``text``.
    """
    wrapped: list[str] = []
    for raw in text.split("\n"):
        raw = raw.rstrip()
        if not raw.strip():
            wrapped.append("")
            continue
        _append_wrapped(wrapped, raw, max_chars)
    return _collapse_blanks(wrapped)


def _append_wrapped(out: list[str], line: str, max_chars: int) -> None:
    words = line.split()
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            out.append(current)
            current = word
        else:
            current = candidate
    if current:
        out.append(current)


def _collapse_blanks(lines: list[str]) -> list[str]:
    result: list[str] = []
    prev_blank = False
    for line in lines:
        if line == "":
            if not prev_blank and result:
                result.append("")
                prev_blank = True
        else:
            result.append(line)
            prev_blank = False
    while result and result[0] == "":
        result.pop(0)
    while result and result[-1] == "":
        result.pop()
    return result


def render_range(
    lines: list[str], start: int, end: int
) -> tuple[str, int, int]:
    """Render an inclusive 1-based line range as numbered text.

    Returns ``(text, actual_start, actual_end)`` where the actual bounds are
    clamped to the document. ``text`` uses ``"<n>: <line>"`` formatting.
    """
    total = len(lines)
    if total == 0:
        return "", 1, 0
    actual_start = max(1, start)
    actual_end = min(total, max(actual_start, end))
    if actual_start > total:
        return "", actual_start, actual_end
    rendered = [
        f"{i}: {lines[i - 1]}" for i in range(actual_start, actual_end + 1)
    ]
    return "\n".join(rendered), actual_start, actual_end


def line_char_lengths(lines: list[str]) -> list[int]:
    """Character length of each prepared line (for sizing assignments)."""
    return [len(line) for line in lines]


def total_chars(lines: list[str]) -> int:
    return sum(line_char_lengths(lines))
