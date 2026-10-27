import asyncio

import pytest
from langchain_core.messages import AIMessage

from provision_graph.graph import ExtractionSettings, build_graph
from provision_graph.models import LineRange, Provision


class FakeModel:
    """Returns a submit_provisions tool call immediately."""

    def __init__(self, submit_args):
        self.submit_args = submit_args

    def bind_tools(self, tools, tool_choice=None):
        return self

    def invoke(self, messages):
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "submit_provisions", "args": self.submit_args, "id": "c1"}
            ],
        )


def _prov(sec, start, end):
    return {
        "title": "t",
        "section_citation": sec,
        "summary": "x",
        "effective_date": "y",
        "irc_sections_amended": [{"cite": "IRC § 1", "note": "amended"}],
        "operative_locations": [{"start": start, "end": end}],
    }


def test_graph_end_to_end_no_overlap():
    # 40 lines; target chars forces ~2+ assignments.
    lines = [f"line {i} with some words here" for i in range(40)]
    # Each assignment returns one provision with non-overlapping locations.
    # The fake model always returns the same submit args, so we give a single
    # provision; overlap is avoided by making locations per-assignment unique
    # via a counter is not possible with a static fake, so instead use a
    # tiny document where there is exactly one assignment.
    settings = ExtractionSettings(assignment_target_chars=10**9)
    model = FakeModel({"provisions": [_prov("Sec. 1", 1, 5)]})
    graph = build_graph(lines, model, settings)
    final = asyncio.run(graph.ainvoke({}))
    assert "assignments" in final
    assert len(final["assignments"]) == 1
    results = final["assignment_results"]
    assert len(results) == 1
    only = next(iter(results.values()))
    assert only.status == "completed"
    assert len(only.provisions) == 1
    assert len(final["reconciled_provisions"]) == 1


def test_graph_multiple_assignments_fanout():
    lines = [f"line {i} with some words here" for i in range(40)]
    # Small target so we get multiple assignments.
    settings = ExtractionSettings(assignment_target_chars=60)
    model = FakeModel({"provisions": []})  # no provisions -> no overlap
    graph = build_graph(lines, model, settings)
    final = asyncio.run(graph.ainvoke({}))
    assert len(final["assignments"]) > 1
    assert len(final["assignment_results"]) == len(final["assignments"])
    assert final["reconciled_provisions"] == []


def test_graph_empty_document():
    graph = build_graph([], FakeModel({"provisions": []}), ExtractionSettings())
    final = asyncio.run(graph.ainvoke({}))
    assert final.get("assignments") == []
    assert final.get("reconciled_provisions") == []


class FlakyModel:
    """Raises on the first N invokes, then returns a valid submit call."""

    def __init__(self, submit_args, fail_times):
        self.submit_args = submit_args
        self.fail_times = fail_times
        self.calls = 0

    def bind_tools(self, tools, tool_choice=None):
        return self

    def invoke(self, messages):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("error code: 503 - engine overloaded")
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "submit_provisions", "args": self.submit_args, "id": "c1"}
            ],
        )


def test_worker_retries_transient_model_errors():
    from provision_graph.models import Assignment
    from provision_extraction.worker import WorkerSettings, run_assignment

    lines = [f"line {i} with some words here" for i in range(20)]
    assignment = Assignment(id="a1", start_line=1, end_line=20)
    model = FlakyModel({"provisions": [_prov("Sec. 1", 1, 5)]}, fail_times=2)
    result = run_assignment(
        assignment,
        lines,
        model,
        WorkerSettings(retry_backoff=0.0, max_model_retries=4),
    )
    assert result.status == "completed"
    assert len(result.provisions) == 1
    assert model.calls == 3


def test_worker_gives_up_after_max_retries():
    from provision_graph.models import Assignment
    from provision_extraction.worker import WorkerSettings, run_assignment

    lines = [f"line {i} with some words here" for i in range(20)]
    assignment = Assignment(id="a1", start_line=1, end_line=20)
    model = FlakyModel({"provisions": []}, fail_times=999)
    result = run_assignment(
        assignment,
        lines,
        model,
        WorkerSettings(retry_backoff=0.0, max_model_retries=3),
    )
    assert result.status == "failed"
    assert "model error" in result.error


def test_graph_survives_failed_assignments():
    from provision_extraction.worker import WorkerSettings

    lines = [f"line {i} with some words here" for i in range(40)]
    settings = ExtractionSettings(
        assignment_target_chars=60,
        worker=WorkerSettings(retry_backoff=0.0, max_model_retries=1),
    )
    model = FlakyModel({"provisions": []}, fail_times=999)
    graph = build_graph(lines, model, settings)
    final = asyncio.run(graph.ainvoke({}))
    assert len(final["assignment_results"]) == len(final["assignments"])
    assert final["reconciled_provisions"] == []
    failed = final["failed_assignments"]
    assert len(failed) == len(final["assignments"])
    assert all(f["error"] for f in failed)
