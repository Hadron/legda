from provision_extraction.text import prepare_lines, render_range


def test_preserves_lines_and_wraps():
    long_line = " ".join(["word" * 5 for _ in range(8)])  # spaced, breakable
    text = "short line\n" + long_line + "\n\n\nfinal"
    lines = prepare_lines(text, max_chars=40)
    assert lines[0] == "short line"
    assert all(len(l) <= 40 for l in lines if l)
    assert lines[-1] == "final"
    # the long spaced line got wrapped into multiple lines
    wrapped = lines[1:-1]
    assert len(wrapped) > 1
    assert " ".join(wrapped).split() == long_line.split()


def test_collapse_blanks_and_trim():
    lines = prepare_lines("\n\n\nA\n\n\n\nB\n\n\n")
    assert lines == ["A", "", "B"]


def test_render_range_numbering():
    lines = ["one", "two", "three", "four"]
    text, s, e = render_range(lines, 2, 3)
    assert text == "2: two\n3: three"
    assert (s, e) == (2, 3)


def test_render_range_clamps():
    lines = ["a", "b"]
    text, s, e = render_range(lines, -5, 10)
    assert s == 1 and e == 2
    assert "1: a" in text and "2: b" in text
