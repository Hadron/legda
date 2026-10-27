from provision_graph.models import LineRange, Provision
from provision_extraction.reconciliation import (
    _group_text,
    group_by_operative_overlap,
)


def _prov(start, end):
    return Provision(
        title="t",
        section_citation="s",
        summary="x",
        effective_date="y",
        operative_locations=[LineRange(start=start, end=end)],
    )


def test_overlap_groups():
    a = _prov(1, 10)
    b = _prov(5, 20)   # overlaps a
    c = _prov(100, 110)  # separate
    groups = group_by_operative_overlap([a, b, c])
    # two groups: {a,b} and {c}
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 2]


def test_transitive():
    a = _prov(1, 5)
    b = _prov(4, 9)
    c = _prov(8, 12)
    groups = group_by_operative_overlap([a, b, c])
    assert len(groups) == 1 and len(groups[0]) == 3


def test_no_locations_all_separate():
    a = Provision(title="t", section_citation="s", summary="x", effective_date="y")
    b = Provision(title="t", section_citation="s", summary="x", effective_date="y")
    groups = group_by_operative_overlap([a, b])
    assert len(groups) == 2


def test_group_text_first_interval_over_budget_not_dropped():
    # Regression: a single interval larger than max_chars must still be
    # included (trimmed at a line boundary), not silently dropped to "".
    lines = ["word " * 20 for _ in range(100)]  # ~100 chars per line
    rng = LineRange(start=1, end=100)
    text = _group_text(lines, [rng], max_chars=1000, context_lines=0)
    assert text != ""
    assert len(text) <= 1000
    # trimmed at a line boundary: last line is a complete "N: ..." line
    import re
    assert re.match(r"^\d+: ", text.splitlines()[-1])


def test_group_text_fills_budget_with_multiple_intervals():
    lines = [f"line number {i} here" for i in range(200)]
    rngs = [LineRange(start=1, end=50), LineRange(start=100, end=150)]
    text = _group_text(lines, rngs, max_chars=5000, context_lines=0)
    # both intervals fit in budget -> both present, separated by blank line
    assert "line number 0 here" in text
    assert "line number 149 here" in text
    assert "\n\n" in text
    assert len(text) <= 5000
