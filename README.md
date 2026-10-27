# Provision Extraction

LangGraph-based extraction of IRS provisions from bills and other documents.

## Design

Three layers, separated by expected portability into the IRS system:

- `src/provision_extraction/` -- portable extraction logic. Intended to run
  inside the IRS with minimal change. No local-only dependencies.
  - `text.py`   raw text -> stable numbered display lines (locations)
  - `tools.py`  retrieve / search / submit_provisions tools
  - `citations.py` best-effort canonical citation normalization
  - `worker.py` bounded per-assignment model/tool loop
  - `assignments.py` contiguous, nonoverlapping initial assignments
  - `reconciliation.py` group overlapping candidates, resolve duplicates
- `src/provision_graph/` -- graph integration. Reconciled with existing IRS
  models on the other side of the firewall.
  - `models.py`  Assignment, Provision, AssignmentResult, LineRange, Citation
  - `state.py`   parent graph state + parallel-result reducer
  - `graph.py`   build_graph: assign -> worker (Send fan-out) -> reconcile
- `src/local/` -- local-only harness for this machine (throw away).
  - `pdf.py`  Docling -> markdown -> page decorations -> pandoc -> plain text
  - `llm.py`  local ChatOpenAI endpoint (gpt-oss:120b)
  - `run.py`  CLI entry point

## The agent loop

Each assignment is a contiguous, nonoverlapping range of prepared lines. The
worker model gets three tools:

- `retrieve(start, end)` -- read an inclusive line range as numbered text.
- `search(query, ...)`  -- grep-like search with numbered context.
- `submit_provisions(provisions)` -- terminal action returning findings.

The worker may retrieve outside its assigned range to find definitions,
effective dates, and related rules. It returns provisions whose
`operative_locations` may span multiple sections. Retrieving another section
does not mean it has "completed" that section; reconciliation handles overlap.

`submit_provisions` is intercepted (not executed); its arguments are validated
against the `Provision` schema. A plain assistant message with no tool call is
answered with a nudge. Forced tool choice is used when the model supports it,
falling back to automatic choice otherwise.

## Locations

Locations are 1-based line numbers into the prepared display lines. Long
source lines are wrapped deterministically; page decorations are ordinary
lines. Every tool, worker, and reconciliation reference uses the same frame.

## Reconciliation

Candidates are grouped by transitive overlap of their `operative_locations`.
Singleton groups pass through. Multi-candidate groups are sent to a model with
the relevant source text to merge near-duplicates and keep distinct
provisions separate.

## Run

    ~/ai/venv/bin/python -m local.run BILLS-119hr5349eas.pdf --out out

Requires `pandoc` and Docling (in `~/ai/venv`) for PDF input, and the local
model endpoint. `--text FILE` runs on a pre-converted text file.

## Tests

    ~/ai/venv/bin/python -m pytest tests/
