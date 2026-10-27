"""Parent graph state and the parallel-result reducer.

The prepared lines, the model, and settings are captured by the graph builder
(closures) rather than stored in state, so the checkpoint stays small and the
reference frame for locations is unambiguous. State carries the work and the
results.
"""

from __future__ import annotations

from typing import Annotated, Any, NotRequired

from typing_extensions import TypedDict

from provision_graph.models import Assignment, AssignmentResult, Provision


def merge_results(
    current: dict[str, AssignmentResult] | None,
    update: dict[str, AssignmentResult] | None,
) -> dict[str, AssignmentResult]:
    """Reducer that merges per-assignment results keyed by assignment id."""
    merged = dict(current or {})
    if update:
        merged.update(update)
    return merged


class GraphState(TypedDict):
    assignments: NotRequired[list[Assignment]]
    assignment_results: Annotated[
        NotRequired[dict[str, AssignmentResult]], merge_results
    ]
    failed_assignments: NotRequired[list[dict]]
    reconciled_provisions: NotRequired[list[Provision]]
