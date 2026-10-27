"""Initial assignment creation.

Splits the prepared lines into contiguous, nonoverlapping blocks of roughly
``target_chars`` characters each. Workers may retrieve outside their assigned
range, but the initial partitions do not overlap.
"""

from __future__ import annotations

from provision_graph.models import Assignment


def make_assignments(lines: list[str], target_chars: int = 15000) -> list[Assignment]:
    n = len(lines)
    if n == 0:
        return []
    assignments: list[Assignment] = []
    pos = 0
    idx = 0
    while pos < n:
        start = pos
        used = 0
        while pos < n:
            used += len(lines[pos]) + 1
            pos += 1
            if used >= target_chars:
                break
        end = pos - 1
        assignments.append(
            Assignment(
                id=f"a{idx:04d}",
                start_line=start + 1,
                end_line=end + 1,
            )
        )
        idx += 1
    return assignments
