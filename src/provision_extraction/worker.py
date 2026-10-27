"""The per-assignment extraction worker.

run_assignment drives one assignment through a bounded model/tool loop:
the worker's assigned range is inlined in its task message, so the first
step is to read it from there; retrieve/search remain available for the
rest of the document and are executed and returned to the model; a
submit_provisions call is intercepted, validated, and ends the loop; a plain
assistant message with no tool call gets a nudge to keep using tools or to
submit; if the step budget is exhausted the assignment fails.

Error handling:
- A tool-call JSON parse failure (the inference engine rejecting the model's
  own malformed tool arguments) is fed back to the model with the parser
  error and the beginning/end of the echoed body, and the loop retries
  immediately without backoff. A bounded number of consecutive parse errors
  fails the assignment.
- Resource/network errors get bounded retries with backoff.

Step tracing:
- An optional ``trace`` callable receives one event dict per notable step
  (tool calls, submissions, errors, nudge) so runs can be diagnosed after
  the fact. Tracing never affects extraction behavior.

The message history is local to this call; it never enters parent graph state.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from provision_graph.models import Assignment, AssignmentResult, Provision
from provision_extraction.text import render_range
from provision_extraction.tools import (
    RETRIEVE_TOOL_NAME,
    SEARCH_TOOL_NAME,
    SUBMIT_TOOL_NAME,
    build_tools,
    execute_tool,
)


@dataclass
class WorkerSettings:
    """Tunables for the extraction worker loop."""

    max_tool_steps: int = 100
    max_retrieve_lines: int = 400
    max_search_results: int = 25
    force_tool_choice: bool = True
    system_note: str = ""
    max_model_retries: int = 3
    retry_backoff: float = 2.0
    max_consecutive_parse_errors: int = 5


SYSTEM_PROMPT = (
    "You extract operative provisions from a federal document (for example "
    "legislation, an executive order, or a regulation) for analysis by the "
    "Internal Revenue Service. You are assigned a range of numbered lines; "
    "the full document is larger, and you may retrieve or search any part "
    "of it.\n"
    "\n"
    "IRS scope: Your findings are used by the Internal Revenue Service. "
    "The IRS administers the U.S. federal tax system and, as an ancillary "
    "function, issues Taxpayer Identification Numbers (TINs). Social "
    "Security Numbers are NOT an IRS function (the Social Security "
    "Administration issues them); treat provisions that only concern SSNs "
    "as out of scope unless the provision also assigns operative work to "
    "the IRS or amends the Code.\n"
    "\n"
    "Extract ONLY provisions that are relevant to the IRS. A provision is "
    "IRS-relevant when it (a) amends, adds to, or repeals the Internal "
    "Revenue Code (26 U.S.C.); (b) concerns a federal tax, penalty, "
    "refund, or other administration of the federal tax system; or "
    "(c) assigns operative work to the IRS or Treasury. Look a bit deeper "
    "than the heading: a provision that names other agencies may still be "
    "IRS-administered (the other agencies often only provide inputs, while "
    "the IRS does the administering), and some parts of the IRC are "
    "administered by other agencies \u2014 so confirm rather than assume. Do "
    "not submit a provision with no IRS-relevant operative content; if "
    "none in your range qualifies, submit an empty list.\n"
    "\n"
    "A provision is an operative unit of the INCOMING DOCUMENT: typically one "
    "numbered section of that document (e.g. 'Sec. 101', 'SECTION 2', "
    "'\u00a7 301.5') that has independent legal effect \u2014 amending, "
    "adding, or repealing text of another instrument, or standing alone as a "
    "mandate, authorization, finding, or report requirement. Text that the "
    "document inserts into or amends in another instrument is CONTENT of the "
    "provision, not separate provisions: if section 504 of the document "
    "adds a new section to a code or regulation, the subsections of that new "
    "section are not provisions \u2014 section 504 is the provision.\n"
    "\n"
    "Ownership: you are responsible for every provision that STARTS within "
    "your assigned line range. If such a provision continues past the end of "
    "your range, retrieve past the end until the provision is complete before "
    "submitting. Do NOT submit a provision that starts before your assigned "
    "range, even if its text continues into your range; another worker owns "
    "it.\n"
    "\n"
    "Tools:\n"
    "- retrieve(start, end): read lines start..end (both inclusive, 1-based).\n"
    "- search(query, before, after, limit): find lines containing query.\n"
    "- submit_provisions(provisions): submit your findings. Call it exactly "
    "once, as your final action, with no other tool calls in the same turn.\n"
)
SYSTEM_PROMPT += (
    "\nMethod:\n"
    "1. Your assigned range is provided in your task message. Identify the "
    "provisions that START in it; if one continues past the range end, "
    "retrieve past the end to complete it.\n"
    "2. For each provision, record the sections of other instruments it "
    "amends or cites, a summary of its legal effect, and its effective-date "
    "rule (say where the date comes from).\n"
    "3. Use retrieve/search on other parts of the document as needed for "
    "context (definitions, related effective-date rules), but "
    "operative_locations should point to the lines expressing the rule.\n"
    "4. When done, call submit_provisions. If no provision that starts in "
    "your range has IRS-relevant operative content, submit an empty list.\n"
)
SYSTEM_PROMPT += (
    "\nCitation forms:\n"
    '- IRC: "IRC \u00a7 NNN" with nested parentheticals, e.g. '
    '"IRC \u00a7 6511(a)(2)". List each specific subsection separately.\n'
    '- Other code: "<title> U.S.C. \u00a7 NNN", e.g. '
    '"22 U.S.C. \u00a7 1741b".\n'
    "Added, removed, and repealed sections count as amended.\n"
)
SYSTEM_PROMPT += (
    "\nFill every field. Citation discipline:\n"
    "- section_citation is the INCOMING DOCUMENT's own section for this "
    "provision (e.g. 'Sec. 504' or 'SECTION 2'), never a section of an "
    "amended or cited instrument (IRC, U.S.C., Treasury regulation, "
    "Circular 230, or similar). If the document's sections are not "
    "numbered, use the document's own heading wording.\n"
    "- irc_sections_amended / irc_sections_cited / other_sections_amended / "
    "other_sections_cited: list the canonical citations as objects {cite, "
    "note}. A new section added to the Code is amended. Use an empty list "
    "if none.\n"
    "- operative_locations: line ranges where the provision's rule is "
    "expressed.\n"
    "- effective_date_evidence: the line ranges that state the effective "
    "date rule.\n"
    "\nLine locations are 1-based inclusive ranges into the numbered "
    "document. Do not invent line numbers; use only lines you actually "
    "retrieved."
)


def _user_prompt(
    assignment: Assignment, total: int, note: str, lines: list[str]
) -> str:
    parts = [
        f"The document has {total} numbered lines.",
        f"Your assignment is lines {assignment.start_line} through "
        f"{assignment.end_line}.",
    ]
    if note:
        parts.append(note)
    parts.append(
        "Your assigned range is included below. Remember: you own every "
        "provision that STARTS in this range, including ones that continue "
        "past its end."
    )
    parts.append(render_range(lines, assignment.start_line, assignment.end_line)[0])
    return "\n".join(parts)


def _mentions_tool_choice(exc: Exception) -> bool:
    text = str(exc).lower()
    return "tool_choice" in text or "tool choice" in text


class _ToolInvoker:
    """Invokes the model with tools, preferring forced tool choice when the
    model supports it and falling back to automatic choice otherwise.

    The fallback triggers only on errors that explicitly indicate the
    tool_choice parameter is unsupported; other errors (including transient
    ones) propagate to the caller's retry handling.
    """

    def __init__(self, model, tools, force_tool_choice: bool):
        self.plain = model.bind_tools(tools)
        self.required = None
        if force_tool_choice:
            try:
                self.required = model.bind_tools(
                    tools, tool_choice="required"
                )
            except Exception:
                self.required = None

    def invoke(self, messages: list[BaseMessage]):
        if self.required is not None:
            try:
                return self.required.invoke(messages)
            except Exception as exc:
                if _mentions_tool_choice(exc):
                    self.required = None
                    return self.plain.invoke(messages)
                raise
        return self.plain.invoke(messages)


def _parse_submission(args: dict[str, Any]) -> list[Provision]:
    """Validate the submit_provisions arguments into Provision models."""
    raw = args.get("provisions", [])
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("provisions must be a list")
    provisions: list[Provision] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each provision must be an object")
        provisions.append(Provision(**item))
    return provisions


_PARSE_ERROR_PATTERNS = [
    re.compile(r"error parsing tool call", re.I),
    re.compile(r"invalid character .{0,60}(after|outside)", re.I),
    re.compile(r"invalid (json|arguments).{0,60}(tool call|function)", re.I),
    re.compile(r"arguments.{0,40}invalid (json|syntax)", re.I),
    re.compile(r"(json|tool call|arguments).{0,40}(parse|parsing).{0,40}"
               r"(error|fail|invalid)", re.I),
    re.compile(r"(parse|parsing).{0,40}(error|fail).{0,40}"
               r"(json|tool call|arguments)", re.I),
]


def _is_parse_error(exc: Exception) -> bool:
    """Best-effort, engine-agnostic detection of a tool-call JSON parse
    failure (as opposed to a resource/network error)."""
    text = str(exc)
    return any(p.search(text) for p in _PARSE_ERROR_PATTERNS)


def _parse_error_feedback(exc: Exception) -> str:
    """Build the message fed back to the model after a parse failure.

    Includes the parser's error detail when identifiable, and the beginning
    and end of the echoed tool-call body when the engine includes it. The
    exception text typically embeds the engine's error body as a Python
    repr, so the echoed value appears quote-escaped (``raw=\\'...\\'``).
    """
    text = str(exc)
    detail = ""
    m = re.search(r"err=((?:[^'\\]|\\.)+)'", text)
    if not m:
        m = re.search(r"err=(.+?)(?:, |$)", text)
    if m:
        detail = m.group(1).replace("\\'", "'").replace("\\n", " ").strip()
    head = tail = ""
    m = re.search(r"raw=\\'(.*?)\\', err=", text, re.DOTALL)
    if not m:
        m = re.search(r"raw='(.*?)(?:', |$)", text, re.DOTALL)
    if m:
        body = m.group(1).replace("\\'", "'").replace("\\n", " ")
        head = body[:200]
        tail = body[-200:]
    parts = [
        "Your last tool call was not executed because its arguments could "
        "not be parsed as valid JSON."
    ]
    if detail:
        parts.append(f"Parser error: {detail}")
    if head:
        parts.append(f"Beginning of what you sent: {head}")
        parts.append(f"...End of what you sent: {tail}")
    parts.append(
        "Retry the same tool call. Its arguments must be a single valid "
        "JSON object, with no characters after the closing brace."
    )
    return "\n".join(parts)


def _emit(trace, assignment: Assignment, event: str, **fields: Any) -> None:
    """Record one trace event. Tracing never affects extraction."""
    if trace is None:
        return
    rec: dict[str, Any] = {
        "ts": round(time.time(), 3),
        "assignment_id": assignment.id,
        "event": event,
    }
    rec.update(fields)
    try:
        trace(rec)
    except Exception:
        pass


def run_assignment(
    assignment: Assignment,
    lines: list[str],
    model,
    settings: WorkerSettings | None = None,
    trace: Callable[[dict], None] | None = None,
) -> AssignmentResult:
    """Run the bounded extraction loop for one assignment."""
    settings = settings or WorkerSettings()
    total = len(lines)
    tools = build_tools(lines)
    invoker = _ToolInvoker(model, tools, settings.force_tool_choice)

    messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(
            content=_user_prompt(
                assignment, total, settings.system_note, lines
            )
        ),
    ]
    _emit(
        trace,
        assignment,
        "assignment_start",
        start_line=assignment.start_line,
        end_line=assignment.end_line,
        total_lines=total,
    )

    parse_streak = 0
    for step in range(settings.max_tool_steps):
        ai: AIMessage | None = None
        last_err: Exception | None = None
        try:
            ai = invoker.invoke(messages)
            parse_streak = 0
        except Exception as exc:
            if _is_parse_error(exc):
                # Malformed tool-call JSON: feed the error back to the model
                # and retry immediately, without backoff. Consumes a step.
                parse_streak += 1
                if parse_streak > settings.max_consecutive_parse_errors:
                    _emit(
                        trace,
                        assignment,
                        "assignment_end",
                        status="failed",
                        steps_used=step + 1,
                        error=(
                            f"tool-call parse error repeated "
                            f"{parse_streak} times: {exc}"
                        ),
                    )
                    return AssignmentResult.failed(
                        assignment.id,
                        f"tool-call parse error repeated {parse_streak} "
                        f"times: {exc}",
                    )
                feedback = _parse_error_feedback(exc)
                messages.append(HumanMessage(content=feedback))
                _emit(
                    trace,
                    assignment,
                    "parse_error_feedback",
                    step=step,
                    streak=parse_streak,
                    detail=str(exc)[:300],
                )
                continue
            # Resource/network error: bounded retries with backoff.
            last_err = exc
            for attempt in range(settings.max_model_retries):
                time.sleep(settings.retry_backoff * (attempt + 1))
                try:
                    ai = invoker.invoke(messages)
                    parse_streak = 0
                    break
                except Exception as exc2:
                    if _is_parse_error(exc2):
                        parse_streak += 1
                        if parse_streak > settings.max_consecutive_parse_errors:
                            _emit(
                                trace,
                                assignment,
                                "assignment_end",
                                status="failed",
                                steps_used=step + 1,
                                error=(
                                    f"tool-call parse error repeated "
                                    f"{parse_streak} times: {exc2}"
                                ),
                            )
                            return AssignmentResult.failed(
                                assignment.id,
                                f"tool-call parse error repeated "
                                f"{parse_streak} times: {exc2}",
                            )
                        feedback = _parse_error_feedback(exc2)
                        messages.append(HumanMessage(content=feedback))
                        _emit(
                            trace,
                            assignment,
                            "parse_error_feedback",
                            step=step,
                            streak=parse_streak,
                            detail=str(exc2)[:300],
                        )
                        break  # conversation advanced; next step re-invokes
                    last_err = exc2
            if ai is None:
                if parse_streak > 0:
                    continue
                _emit(
                    trace,
                    assignment,
                    "assignment_end",
                    status="failed",
                    steps_used=step + 1,
                    error=(
                        f"model error after {settings.max_model_retries} "
                        f"retries: {last_err}"
                    ),
                )
                return AssignmentResult.failed(
                    assignment.id,
                    f"model error after {settings.max_model_retries} "
                    f"retries: {last_err}",
                )
        if not isinstance(ai, AIMessage):
            ai = AIMessage(content=str(getattr(ai, "content", ai)))
        messages.append(ai)

        calls = list(getattr(ai, "tool_calls", None) or [])
        if not calls:
            messages.append(
                HumanMessage(
                    content=(
                        "You did not call a tool. If you still need "
                        "information, use retrieve or search. If you are "
                        "done, call submit_provisions with your findings "
                        "(an empty list if none)."
                    )
                )
            )
            _emit(trace, assignment, "no_tool_call", step=step)
            continue

        submit_calls = [
            c for c in calls if c.get("name") == SUBMIT_TOOL_NAME
        ]
        if submit_calls:
            if len(submit_calls) == 1 and len(calls) == 1:
                call = submit_calls[0]
                try:
                    provisions = _parse_submission(
                        call.get("args", {}) or {}
                    )
                except Exception as exc:
                    messages.append(
                        HumanMessage(
                            content=(
                                "submit_provisions arguments were invalid: "
                                f"{exc}. Fix the arguments and call "
                                "submit_provisions again by itself."
                            )
                        )
                    )
                    _emit(
                        trace,
                        assignment,
                        "submit_invalid",
                        step=step,
                        error=str(exc)[:300],
                    )
                    continue
                _emit(
                    trace,
                    assignment,
                    "submit_ok",
                    step=step,
                    n_provisions=len(provisions),
                )
                _emit(
                    trace,
                    assignment,
                    "assignment_end",
                    status="completed",
                    steps_used=step + 1,
                    n_provisions=len(provisions),
                )
                return AssignmentResult.completed(
                    assignment.id, provisions
                )
            messages.append(
                HumanMessage(
                    content=(
                        "Do not mix submit_provisions with other tool "
                        "calls. Finish any retrieve/search first, then "
                        "call submit_provisions alone on a later turn."
                    )
                )
            )
            _emit(trace, assignment, "submit_mixed", step=step)
            continue

        for call in calls:
            name = call.get("name")
            call_id = call.get("id") or ""
            args = call.get("args", {}) or {}
            try:
                result = execute_tool(name, args, lines)
            except Exception as exc:
                result = f"Tool error: {exc}"
            messages.append(
                ToolMessage(content=result, tool_call_id=call_id)
            )
            _emit(
                trace,
                assignment,
                "tool_call",
                step=step,
                name=name,
                args=args,
                result_chars=len(result),
            )

    _emit(
        trace,
        assignment,
        "assignment_end",
        status="failed",
        steps_used=settings.max_tool_steps,
        error=(
            f"step budget exhausted after {settings.max_tool_steps} steps"
        ),
    )
    return AssignmentResult.failed(
        assignment.id,
        f"step budget exhausted after {settings.max_tool_steps} steps",
    )
