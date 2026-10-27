"""Reconcile provision candidates from parallel assignments.

Candidates are grouped by transitive overlap of their operative locations.
Singleton groups pass through unchanged. Multi-candidate groups are sent to a
model (candidates only, whose fields are self-contained for a merge) to merge
near-duplicates, keep genuinely distinct provisions separate, and return the
final set. Optionally the group's source text can be included.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from provision_graph.models import Provision
from provision_extraction.text import render_range


@dataclass
class ReconcileSettings:
    include_source: bool = False
    max_group_chars: int = 40000
    min_context_lines: int = 2
    max_retries: int = 2


class ReconcileOutput(BaseModel):
    provisions: list[Provision] = Field(default_factory=list)


class _DSU:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def group_by_operative_overlap(
    candidates: list[Provision],
) -> list[list[int]]:
    """Return groups of candidate indices connected by operative overlap."""
    n = len(candidates)
    dsu = _DSU(n)
    for i in range(n):
        ri = candidates[i].operative_locations
        if not ri:
            continue
        for j in range(i + 1, n):
            rj = candidates[j].operative_locations
            if not rj:
                continue
            if any(a.overlaps(b) for a in ri for b in rj):
                dsu.union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(i)
    return list(groups.values())


def _ranges_for_group(candidates: list[Provision]) -> list:
    ranges = []
    for c in candidates:
        ranges.extend(c.operative_locations)
        ranges.extend(c.supporting_locations)
    return ranges


def _group_text(
    lines: list[str],
    ranges: list,
    max_chars: int,
    context_lines: int,
) -> str:
    """Render the union of ranges (with small context) as numbered text.

    Fills up to ``max_chars`` with whole lines. The first interval always
    participates (trimmed at a line boundary if it alone exceeds the
    budget); later intervals are dropped only when the budget is spent.
    """
    if not ranges:
        return ""
    total = len(lines)
    intervals = []
    for r in ranges:
        s = max(1, r.start - context_lines)
        e = min(total, r.end + context_lines)
        intervals.append((s, e))
    # merge overlapping/adjacent intervals
    intervals.sort()
    merged = []
    for s, e in intervals:
        if merged and s <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    parts = []
    used = 0
    for s, e in merged:
        if used > 0 and used >= max_chars:
            break
        budget = max_chars if used == 0 else max_chars - used - 2
        text, _, _ = render_range(lines, s, e)
        if not text:
            continue
        if len(text) > budget:
            # trim at a line boundary
            lo, hi = s, e
            while hi - lo > 1:
                mid = (lo + hi) // 2
                t, _, _ = render_range(lines, s, mid)
                if len(t) <= budget:
                    lo = mid
                else:
                    hi = mid
            text, _, _ = render_range(lines, s, lo)
        parts.append(text)
        used += len(text) + (2 if len(parts) > 1 else 0)
    return "\n\n".join(parts)


RECONCILE_PROMPT = (
    "You are reconciling provision candidates extracted by independent "
    "workers. The candidate provisions below overlap in their line "
    "locations. They may be near-duplicates that should merge into a single "
    "provision, or genuinely distinct provisions that should stay separate "
    "(distinct provisions have different section_citation values).\n"
    "\n"
    "Merge near-duplicates into a single provision and keep genuinely "
    "distinct provisions separate, then return the final set. For each "
    "returned provision: preserve the candidates' canonical citations; use "
    "the union of the candidates' line locations so operative_locations span "
    "the full provision; keep effective-date rules in effective_date with "
    "their line ranges in effective_date_evidence; keep summaries concise "
    "but complete.\n"
    "\n"
    "Output discipline: return only the provisions array. Do not add "
    "commentary, and do not restate the candidates beyond the values of the "
    "final provisions.\n"
    "\n"
    "If all candidates are spurious, return an empty list.\n"
)


def reconcile_provisions(
    candidates: list[Provision],
    model,
    settings: ReconcileSettings | None = None,
    trace=None,
    lines: list[str] | None = None,
) -> list[Provision]:
    """Merge overlapping candidate provisions.

    Only candidates that overlap in operative locations are presented to
    the model; singleton groups pass through with no call. The payload
    carries the candidates themselves, whose fields are self-contained for
    a merge (duplicates share section_citation; distinct provisions do
    not). When ``settings.include_source`` is set and ``lines`` is given,
    the group's union source text is included as well.
    """
    settings = settings or ReconcileSettings()

    def _emit(event: str, **fields) -> None:
        if trace is None:
            return
        rec = {"ts": round(time.time(), 3), "event": event}
        rec.update(fields)
        try:
            trace(rec)
        except Exception:
            pass

    if not candidates:
        _emit("reconcile_done", n_candidates=0, n_groups=0, n_result=0)
        return []
    groups = group_by_operative_overlap(candidates)
    final: list[Provision] = []
    for group in groups:
        if len(group) == 1:
            final.append(candidates[group[0]])
            continue
        group_cands = [candidates[i] for i in group]
        # Drop provenance from the payload so the model is not tempted to
        # copy or invent an assignment_id for merged provisions.
        cand_payload = [
            {k: v for k, v in c.model_dump().items() if k != "assignment_id"}
            for c in group_cands
        ]
        payload: dict[str, Any] = {"candidates": cand_payload}
        if settings.include_source and lines is not None:
            payload["source_text"] = _group_text(
                lines,
                _ranges_for_group(group_cands),
                settings.max_group_chars,
                settings.min_context_lines,
            )
        prompt = RECONCILE_PROMPT + "\n" + json.dumps(payload, indent=2)
        # Force function calling: the JSON-schema response_format path is
        # what makes thinking models spiral into huge outputs on this
        # endpoint; tool-call mode converges cleanly. Function calling is
        # also the most portable structured-output mode across engines.
        structured = model.with_structured_output(
            ReconcileOutput, method="function_calling"
        )
        out = None
        last_exc: Exception | None = None
        attempts = settings.max_retries + 1
        for _ in range(attempts):
            try:
                out = structured.invoke(prompt)
                break
            except Exception as exc:
                last_exc = exc
        if out is None:
            _emit(
                "reconcile_group_failed" if last_exc else "reconcile_group_none",
                n_candidates=len(group_cands),
                attempts=attempts,
                prompt_chars=len(prompt),
                error=str(last_exc)[:300] if last_exc else None,
            )
            final.extend(group_cands)
            continue
        for p in out.provisions:
            p.assignment_id = None
        _emit(
            "reconcile_group_merged",
            n_candidates=len(group_cands),
            n_result=len(out.provisions),
        )
        final.extend(out.provisions)
    _emit(
        "reconcile_done",
        n_candidates=len(candidates),
        n_groups=len(groups),
        n_result=len(final),
    )
    return final
