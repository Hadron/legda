"""Local entry point: run the extraction graph on a PDF or text file."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from provision_extraction.reconciliation import ReconcileSettings
from provision_extraction.text import prepare_lines
from provision_extraction.worker import WorkerSettings
from provision_graph.document import TextDocument
from provision_graph.graph import ExtractionSettings, build_graph
from local.llm import get_model, get_reconcile_model


def _to_text(args) -> str:
    if args.text:
        return Path(args.text).read_text(encoding="utf-8")
    from local.pdf import pdf_to_text
    return pdf_to_text(args.pdf, ocr=args.ocr)


def _cite_lines(items) -> str:
    if not items:
        return "  (none)\n"
    out = []
    for c in items:
        if isinstance(c, dict):
            cite = c.get("cite", "")
            note = c.get("note")
        else:
            cite = getattr(c, "cite", "")
            note = getattr(c, "note", None)
        out.append(f"  - {cite}" + (f" \u2014 {note}" if note else ""))
    return "\n".join(out) + "\n"


def render_markdown(provisions, source_label: str) -> str:
    lines = ["# Provisions", ""]
    lines.append(f"Source: {source_label}")
    lines.append(f"Provisions: {len(provisions)}")
    lines.append("")
    for i, p in enumerate(provisions, 1):
        d = p if isinstance(p, dict) else p.model_dump()
        title = d.get("title", f"Provision {i}")
        sec = d.get("section_citation", "")
        lines.append(f"## {i}. {title}")
        lines.append("")
        if sec:
            lines.append(f"Citation: {sec}")
            lines.append("")
        lines.append("Summary:")
        lines.append(f"  {d.get('summary','')}")
        lines.append("")
        lines.append("Effective date:")
        lines.append(f"  {d.get('effective_date','')}")
        lines.append("")
        lines.append("IRC sections amended:")
        lines.append(_cite_lines(d.get("irc_sections_amended")))
        lines.append("IRC sections cited:")
        lines.append(_cite_lines(d.get("irc_sections_cited")))
        lines.append("Other sections amended:")
        lines.append(_cite_lines(d.get("other_sections_amended")))
        lines.append("Other sections cited:")
        lines.append(_cite_lines(d.get("other_sections_cited")))
    return "\n".join(lines)


def _trace_factory(outdir: Path, verbose: bool):
    """Per-assignment JSONL trace sinks under outdir/traces/.

    Each sink writes one JSON object per event and prints a compact line to
    stdout for live monitoring. The file closes when the assignment ends.
    """
    traces = outdir / "traces"
    traces.mkdir(parents=True, exist_ok=True)

    def make_sink(assignment_id: str):
        fh = open(traces / f"{assignment_id}.jsonl", "a", encoding="utf-8")
        closed = {"v": False}

        def sink(event: dict) -> None:
            if closed["v"]:
                return
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            fh.flush()
            if verbose:
                _print_trace_line(assignment_id, event)
            if event.get("event") in ("assignment_end", "reconcile_done"):
                closed["v"] = True
                fh.close()

        return sink

    return make_sink


def _print_trace_line(assignment_id: str, event: dict) -> None:
    kind = event.get("event")
    step = event.get("step")
    if kind == "tool_call":
        args = event.get("args") or {}
        detail = (
            f"{args.get('start')}-{args.get('end')}"
            if "start" in args
            else str(args.get("query", ""))[:40]
        )
        print(
            f"[{assignment_id}] step {step} {kind} {detail} "
            f"-> {event.get('result_chars', '?')} chars",
            flush=True,
        )
    elif kind in ("submit_ok", "submit_invalid", "parse_error_feedback",
                  "no_tool_call", "submit_mixed"):
        extra = (
            f"n={event.get('n_provisions')}"
            if kind == "submit_ok"
            else event.get("error") or event.get("detail") or ""
        )
        print(f"[{assignment_id}] step {step} {kind} {str(extra)[:120]}",
              flush=True)
    elif kind == "assignment_start":
        print(
            f"[{assignment_id}] start lines "
            f"{event.get('start_line')}-{event.get('end_line')}",
            flush=True,
        )
    elif kind == "assignment_end":
        status = event.get("status")
        extra = event.get("n_provisions")
        if status == "failed":
            extra = event.get("error", "")
        print(
            f"[{assignment_id}] end {status} "
            f"steps={event.get('steps_used')} {extra}",
            flush=True,
        )


def _run_graph(document, model, settings, verbose: bool,
               max_concurrency: int, trace_factory, reconcile_model=None):
    graph = build_graph(
        model, settings,
        trace_sink_factory=trace_factory,
        reconcile_model=reconcile_model,
    )
    if verbose:
        print(
            f"Running graph (async, parallel workers, "
            f"max_concurrency={max_concurrency})...",
            flush=True,
        )
    final = asyncio.run(
        graph.ainvoke(
            {"document": document},
            config={"max_concurrency": max_concurrency},
        )
    )
    return final


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract IRS provisions")
    ap.add_argument("pdf", nargs="?", default="BILLS-119hr5349eas.pdf")
    ap.add_argument("--text", help="Use a text file instead of PDF")
    ap.add_argument("--ocr", action="store_true", help="Run OCR in Docling")
    ap.add_argument("--target-chars", type=int, default=15000)
    ap.add_argument("--out", default="out")
    ap.add_argument("--model", default="Qwen/Qwen3.8-27B")
    ap.add_argument("--base-url", default="http://konishi.lan:8080/v1")
    ap.add_argument("--max-steps", type=int, default=100)
    ap.add_argument("--max-tokens", type=int, default=32768)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument(
        "--max-concurrency", type=int, default=10,
        help="Max concurrent worker/model calls (langgraph max_concurrency)",
    )
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    verbose = not args.quiet
    text = _to_text(args)
    doc = TextDocument(prepare_lines(text))
    if verbose:
        print(
            f"Prepared {len(doc.lines)} lines "
            f"({len(text)} chars) from source.",
            flush=True,
        )

    model = get_model(
        model_name=args.model,
        base_url=args.base_url,
        timeout=args.timeout,
        max_tokens=args.max_tokens,
    )
    reconcile_model = get_reconcile_model(
        model_name=args.model,
        base_url=args.base_url,
        timeout=args.timeout,
        max_tokens=args.max_tokens,
    )
    settings = ExtractionSettings(
        assignment_target_chars=args.target_chars,
        worker=WorkerSettings(max_tool_steps=args.max_steps),
        reconcile=ReconcileSettings(),
    )
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    trace_factory = _trace_factory(outdir, verbose)
    final = _run_graph(
        doc, model, settings, verbose, args.max_concurrency,
        trace_factory, reconcile_model=reconcile_model,
    )

    provisions = final.get("reconciled_provisions", [])
    failures = final.get("failed_assignments", [])
    json_path = outdir / "provisions.json"
    md_path = outdir / "provisions.md"
    payload = [p.model_dump() if hasattr(p, "model_dump") else p for p in provisions]
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    md_path.write_text(render_markdown(provisions, args.pdf))
    if failures:
        fail_path = outdir / "failed_assignments.json"
        fail_path.write_text(
            json.dumps(failures, indent=2, ensure_ascii=False)
        )
    if verbose:
        print(f"Wrote {len(provisions)} provisions.")
        print(f"  JSON: {json_path}")
        print(f"  MD:   {md_path}")
        if failures:
            print(f"  FAIL: {len(failures)} assignment(s) failed:")
            for f in failures:
                print(f"    {f['assignment_id']}: {f['error']}")
            print(f"  {fail_path}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
