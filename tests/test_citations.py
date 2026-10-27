from provision_extraction.citations import normalize_citation, classify


def test_usc_irc():
    assert normalize_citation("26 U.S.C. 6511") == "IRC § 6511"
    assert (
        normalize_citation("26 U.S.C. 6511(a)(2)")
        == "IRC § 6511(a)(2)"
    )


def test_other_usc():
    assert (
        normalize_citation("22 U.S.C. 1741b")
        == "22 U.S.C. § 1741b"
    )


def test_irc_named():
    assert (
        normalize_citation("Internal Revenue Code section 6511(a)")
        == "IRC § 6511(a)"
    )


def test_unknown_unchanged():
    assert normalize_citation("some plain text") == "some plain text"
