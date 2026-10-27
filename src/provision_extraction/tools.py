"""Retrieval and submission tools exposed to the extraction worker.

Three tools are presented to the model:

- ``retrieve``: read an inclusive 1-based line range as numbered text.
- ``search``: find lines containing a query (grep-like, with context).
- ``submit_provisions``: terminal action that returns the extracted
  provisions. The worker intercepts this call instead of executing it.

``build_tools`` returns LangChain tools bound to a specific prepared-lines
list so they can be passed to ``model.bind_tools``. ``execute_tool`` runs the
non-terminal tools manually so the worker controls the loop.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool

from provision_extraction.text import render_range
from provision_graph.models import Provision

SUBMIT_TOOL_NAME = "submit_provisions"
RETRIEVE_TOOL_NAME = "retrieve"
SEARCH_TOOL_NAME = "search"


def search_lines(
    lines: list[str],
    query: str,
    before: int = 3,
    after: int = 3,
    limit: int = 25,
) -> str:
    """Case-insensitive substring search with numbered context around hits."""
    needle = query.strip().lower()
    if not needle:
        return "Empty query."
    hits = [
        i for i, line in enumerate(lines, start=1) if needle in line.lower()
    ]
    if not hits:
        return f"No matches for {query!r} in {len(lines)} lines."
    total = len(hits)
    shown = hits[:limit]
    parts = [f"{total} match(es) for {query!r}; showing first {len(shown)}:"]
    for h in shown:
        start = max(1, h - before)
        end = min(len(lines), h + after)
        text, _, _ = render_range(lines, start, end)
        parts.append(f"--- match at line {h} ---\n{text}")
    if total > len(shown):
        parts.append(f"... and {total - len(shown)} more match(es).")
    return "\n\n".join(parts)


def build_tools(lines: list[str]) -> list[BaseTool]:
    """Build the worker's tools bound to ``lines``."""

    @tool
    def retrieve(start: int, end: int) -> str:
        """Read lines from start to end (both inclusive, 1-based) as numbered
        text. Example: retrieve(40, 80) returns lines 40 through 80."""
        text, _, _ = render_range(lines, start, end)
        if not text:
            total = len(lines)
            return f"No text in that range; document has {total} lines."
        return text

    @tool
    def search(query: str, before: int = 3, after: int = 3, limit: int = 25) -> str:
        """Find lines containing the query (case-insensitive). Returns numbered
        context lines around each match. Use this to locate a term, then
        retrieve a range to read it in full."""
        return search_lines(lines, query, before=before, after=after, limit=limit)

    @tool
    def submit_provisions(provisions: list[Provision]) -> str:
        """Submit the extracted provisions for this assignment. Call this
        exactly once, as the final action, without any other tool calls in the
        same turn. Use an empty list if no IRS-relevant provisions were found
        in the assigned range."""
        return "submitted"

    return [retrieve, search, submit_provisions]


def execute_tool(name: str, args: dict[str, Any], lines: list[str]) -> str:
    """Execute a non-terminal tool by name. Raises ValueError for submit."""
    if name == RETRIEVE_TOOL_NAME:
        start = int(args.get("start", 1))
        end = int(args.get("end", start))
        text, _, _ = render_range(lines, start, end)
        if not text:
            return f"No text in that range; document has {len(lines)} lines."
        return text
    if name == SEARCH_TOOL_NAME:
        return search_lines(
            lines,
            str(args.get("query", "")),
            before=int(args.get("before", 3)),
            after=int(args.get("after", 3)),
            limit=int(args.get("limit", 25)),
        )
    raise ValueError(f"Tool {name!r} is not manually executable here.")
