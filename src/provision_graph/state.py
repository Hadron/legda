"""Parent graph state and the parallel-result reducer.

The document (which carries the prepared lines) is supplied via the initial
state at invocation time, so the graph can be built before the document is
known. The model and settings are captured by the graph builder (closures).
State carries the work and the results. The document is read-only shared
state; workers must not mutate it.
"""

from __future__ import annotations

from typing import Annotated, Any, NotRequired

from typing_extensions import TypedDict

from provision_graph.document import TextDocument
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
    document: NotRequired[TextDocument]
    assignments: NotRequired[list[Assignment]]
    assignment_results: Annotated[
        NotRequired[dict[str, AssignmentResult]], merge_results
    ]
    failed_assignments: NotRequired[list[dict]]
    reconciled_provisions: NotRequired[list[Provision]]
