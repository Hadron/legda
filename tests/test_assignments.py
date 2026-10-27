from provision_extraction.assignments import make_assignments


def test_contiguous_nonoverlapping():
    lines = [f"line {i} " + ("x" * 50) for i in range(100)]
    a = make_assignments(lines, target_chars=200)
    assert a[0].start_line == 1
    assert a[-1].end_line == len(lines)
    for prev, cur in zip(a, a[1:]):
        assert prev.end_line + 1 == cur.start_line


def test_empty():
    assert make_assignments([], target_chars=100) == []
