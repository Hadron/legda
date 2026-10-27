"""Build the extraction graph.

Nodes:
- assign: create contiguous, nonoverlapping assignments from the lines.
- worker: run one assignment's bounded extraction loop (fan out via Send).
- reconcile: resolve potential duplicate candidates into final provisions.

The prepared lines, the model, and settings are captured by the builder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from provision_extraction.assignments import make_assignments
from provision_extraction.reconciliation import (
    ReconcileSettings,
    reconcile_provisions,
)
from provision_extraction.text import prepare_lines
from provision_extraction.worker import WorkerSettings, run_assignment
from provision_graph.models import Provision
from provision_graph.state import GraphState


@dataclass
class ExtractionSettings:
    assignment_target_chars: int = 15000
    worker: WorkerSettings = field(default_factory=WorkerSettings)
    reconcile: ReconcileSettings = field(default_factory=ReconcileSettings)


def build_graph(
    lines: list[str],
    model,
    settings: ExtractionSettings | None = None,
    trace_sink_factory=None,
    reconcile_model=None,
):
    """Build the extraction graph.

    ``trace_sink_factory`` (optional) maps an assignment id to a callable
    that receives step-trace event dicts for that assignment (see
    provision_extraction.worker.run_assignment). Returns None to disable
    tracing for an assignment.

    ``reconcile_model`` (optional) is a separate model client used for the
    reconciliation step; defaults to ``model``. Local runs pass a client
    with thinking disabled for the mechanical merge work.
    """
    settings = settings or ExtractionSettings()
    reconcile_model = reconcile_model or model

    def assign_node(state: GraphState) -> dict:
        assignments = make_assignments(
            lines, settings.assignment_target_chars
        )
        return {"assignments": assignments}

    def route_assignments(state: GraphState):
        assignments = state.get("assignments") or []
        if not assignments:
            return "reconcile"
        return [Send("worker", a) for a in assignments]

    def worker_node(assignment) -> dict:
        sink = None
        if trace_sink_factory is not None:
            sink = trace_sink_factory(assignment.id)
        result = run_assignment(
            assignment, lines, model, settings.worker, trace=sink
        )
        return {"assignment_results": {assignment.id: result}}

    def reconcile_node(state: GraphState) -> dict:
        results = state.get("assignment_results") or {}
        failed = [
            {"assignment_id": r.assignment_id, "error": r.error}
            for r in results.values()
            if r.status == "failed"
        ]
        candidates: list[Provision] = []
        for r in results.values():
            if r.status != "failed":
                candidates.extend(r.provisions)
        sink = None
        if trace_sink_factory is not None:
            sink = trace_sink_factory("reconcile")
        final = reconcile_provisions(
            candidates, reconcile_model, settings.reconcile,
            trace=sink, lines=lines,
        )
        return {"failed_assignments": failed, "reconciled_provisions": final}

    graph = StateGraph(GraphState)
    graph.add_node("assign", assign_node)
    graph.add_node("worker", worker_node)
    graph.add_node("reconcile", reconcile_node)
    graph.add_edge(START, "assign")
    graph.add_conditional_edges(
        "assign", route_assignments, ["worker", "reconcile"]
    )
    graph.add_edge("worker", "reconcile")
    graph.add_edge("reconcile", END)
    return graph.compile()
