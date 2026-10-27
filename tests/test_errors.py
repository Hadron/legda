"""Tests for the worker's portable error classification, parse-error
feedback, and step tracing."""

from langchain_core.messages import AIMessage

from provision_extraction.worker import (
    WorkerSettings,
    _is_parse_error,
    _parse_error_feedback,
    run_assignment,
)
from provision_graph.document import TextDocument
from provision_graph.models import Assignment


def _make_parse_error_exc() -> Exception:
    """Reproduce the inference-engine 500 for an unparseable tool call."""
    import json

    inner = (
        "error parsing tool call: raw='"
        + json.dumps({"provisions": [{"title": "t"}]}, indent=2)
        + "}', err=invalid character '}' after top-level value"
    )
    body = {
        "error": {
            "message": inner,
            "type": "api_error",
            "param": None,
            "code": None,
        }
    }
    return Exception(f"Error code: 500 - {body!r}")


def test_is_parse_error_detects_engine_variants():
    assert _is_parse_error(_make_parse_error_exc())
    assert _is_parse_error(Exception(
        "Error code: 400 - {'error': {'message': "
        "'Invalid JSON in tool call arguments: unexpected token'}}"
    ))
    assert _is_parse_error(Exception(
        "400: messages.0.tool_calls.0.function.arguments: Invalid JSON"
    ))
    assert not _is_parse_error(Exception(
        "Error code: 503 - {'error': {'message': 'engine overloaded'}}"
    ))
    assert not _is_parse_error(Exception("Request timed out"))


def test_parse_error_feedback_includes_detail_and_body_edges():
    exc = _make_parse_error_exc()
    feedback = _parse_error_feedback(exc)
    assert "could not be parsed as valid JSON" in feedback
    assert "invalid character" in feedback
    assert '"provisions"' in feedback
    assert "no characters after the closing brace" in feedback


class FlakyParseModel:
    """Raises a parse-error 500 on the first invoke, then submits."""

    def __init__(self, submit_args, parse_fail_times=1):
        self.submit_args = submit_args
        self.parse_fail_times = parse_fail_times
        self.calls = 0

    def bind_tools(self, tools, tool_choice=None):
        return self

    def invoke(self, messages):
        self.calls += 1
        if self.calls <= self.parse_fail_times:
            raise _make_parse_error_exc()
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "submit_provisions", "args": self.submit_args,
                 "id": "c1"}
            ],
        )


def _assignment():
    return Assignment(id="a1", start_line=1, end_line=10)


def test_parse_error_retries_without_backoff_and_completes():
    lines = [f"line {i} with some words here" for i in range(10)]
    model = FlakyParseModel({"provisions": []})
    # A large backoff would stall the test if the parse path slept.
    result = run_assignment(
        _assignment(),
        TextDocument(lines),
        model,
        WorkerSettings(retry_backoff=999.0),
    )
    assert result.status == "completed"
    assert model.calls == 2


def test_repeated_parse_errors_fail_assignment():
    lines = [f"line {i} with some words here" for i in range(10)]
    model = FlakyParseModel({"provisions": []}, parse_fail_times=999)
    result = run_assignment(
        _assignment(),
        TextDocument(lines),
        model,
        WorkerSettings(retry_backoff=999.0, max_consecutive_parse_errors=2),
    )
    assert result.status == "failed"
    assert "parse error" in result.error


def test_trace_events_recorded():
    lines = [f"line {i} with some words here" for i in range(10)]
    model = FlakyParseModel({"provisions": []})
    events: list[dict] = []
    result = run_assignment(
        _assignment(),
        TextDocument(lines),
        model,
        WorkerSettings(retry_backoff=0.0),
        trace=events.append,
    )
    assert result.status == "completed"
    kinds = [e["event"] for e in events]
    assert kinds[0] == "assignment_start"
    assert kinds[-1] == "assignment_end"
    assert "parse_error_feedback" in kinds
    assert "submit_ok" in kinds
    end = events[-1]
    assert end["status"] == "completed"
    assert end["assignment_id"] == "a1"
