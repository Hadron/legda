"""Unit tests for XmlDocument (USLM flattener, outline, assignments, navigate)."""

from __future__ import annotations

import json
import textwrap

import pytest

from provision_graph.xml_document import XmlDocument

NS = "http://schemas.gpo.gov/xml/uslm"


def _small_xml() -> str:
    return textwrap.dedent(
        f"""
        <pLaw xmlns="{NS}">
          <preface>
            <page display="no">?71</page>
            <centerRunningHead>TEST LAW 99-99</centerRunningHead>
            <docNumber>99</docNumber>
          </preface>
          <main>
            <longTitle>
              <docTitle>An Act</docTitle>
              <officialTitle>To do a test thing.</officialTitle>
            </longTitle>
            <enactingFormula>Be it enacted by the Senate and House of
              Representatives of the United States of America in Congress
              assembled:</enactingFormula>
            <section identifier="/us/pl/99/99/s1">
              <num>SECTION 1. </num>
              <heading>TABLE OF CONTENTS.</heading>
              <content>
                <p>The table of contents of this Act is as follows:</p>
                <toc>
                  <referenceItem role="section"><designator>Sec. 1.
                    </designator><label>Table of contents.</label></referenceItem>
                  <referenceItem role="title"><designator>TITLE I—</designator>
                    <label>TEST</label></referenceItem>
                  <referenceItem role="section"><designator>Sec. 101.
                    </designator><label>First section.</label></referenceItem>
                </toc>
              </content>
            </section>
            <title identifier="/us/pl/99/99/tI">
              <num>TITLE I—</num>
              <heading>TEST</heading>
              <subtitle identifier="/us/pl/99/99/tI/stA">
                <num>Subtitle A—</num>
                <heading>Alpha</heading>
                <section identifier="/us/pl/99/99/tI/stA/s101">
                  <num>SEC. 101. </num>
                  <heading>FIRST SECTION.</heading>
                  <content>
                    <p>Section 1 of <ref>H.R. 1</ref> is amended by
                      striking "old" and inserting "new".</p>
                    <page identifier="/us/stat/99/100">99 STAT. 100</page>
                    <paragraph>
                      <num>(1) </num>
                      <chapeau>For purposes of this section—</chapeau>
                      <subparagraph><num>(A) </num><term>term one</term>
                        means the first thing.</subparagraph>
                      <subparagraph><num>(B) </num>term two means the
                        second thing.</subparagraph>
                    </paragraph>
                  </content>
                </section>
                <section identifier="/us/pl/99/99/tI/stA/s102">
                  <num>SEC. 102. </num>
                  <heading>SECOND SECTION.</heading>
                  <content><p>Short second section body.</p></content>
                </section>
              </subtitle>
              <section identifier="/us/pl/99/99/tI/s201">
                <num>SEC. 201. </num>
                <heading>SECTION UNDER DIVISION DIRECTLY.</heading>
                <content><p>Body of section 201.</p></content>
              </section>
            </title>
            <title identifier="/us/pl/99/99/tII">
              <num>TITLE II—</num>
              <heading>BIG</heading>
              <subtitle identifier="/us/pl/99/99/tII/stB">
                <num>Subtitle B—</num>
                <heading>More</heading>
                <section>
                  <num>“SEC. 310. </num>
                  <heading>NO-ID SECTION.</heading>
                  <content><p>Body without identifier.</p></content>
                </section>
              </subtitle>
              <section identifier="/us/pl/99/99/tII/s301">
                <num>SEC. 301. </num>
                <heading>HUGE SECTION.</heading>
                <content><p>{'Huge word. ' * 4000}</p></content>
              </section>
            </title>
            <action><actionDescription>Approved</actionDescription>
              <date date="2025-07-04">July 4, 2025</date>.</action>
          </main>
        </pLaw>
        """
    ).strip()


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    p = tmp_path_factory.mktemp("xml") / "law.xml"
    p.write_text(_small_xml(), encoding="utf-8")
    return XmlDocument.from_uslm(p)


def _nav(doc, id="", cursor=""):
    tools = doc.optional_tools()
    assert len(tools) == 1 and tools[0].name == "navigate"
    return json.loads(tools[0].func(id=id, cursor=cursor))


class TestFlatten:
    def test_plain_text_no_markup(self, doc):
        joined = "\n".join(doc.lines)
        for bad in ("<section", "<?", "xmlns", "uslm:", "/us/pl/99/99/"):
            assert bad not in joined, f"{bad!r} leaked into flattened text"
        assert all(len(l) <= 120 for l in doc.lines)

    def test_text_and_citations_preserved(self, doc):
        joined = "\n".join(doc.lines)
        for phrase in (
            "H.R. 1",
            "FIRST SECTION",
            "Be it enacted by the Senate",
            "To do a test thing",
            "An Act",
            "term one means the first thing",
            "For purposes of this section—",
            "99 STAT. 100",
            "Approved",
            "Table of contents",
            "Body without identifier",
        ):
            assert phrase in joined, f"{phrase!r} missing"

    def test_page_markers(self, doc):
        pages = [l for l in doc.lines if l.startswith("[Page")]
        assert any(l == "[Page 1]" for l in pages)  # junk '?71' head dropped
        assert any(l.startswith("[Page") and "99 STAT. 100" in l for l in pages)
        nums = [l.split("]")[0] for l in pages]
        assert nums == [f"[Page {i}" for i in range(1, len(pages) + 1)]

    def test_toc_identified(self, doc):
        assert doc.toc_section is not None
        assert doc.toc_section.id == "/us/pl/99/99/s1"
        assert doc.toc_section.is_toc
        assert doc.toc_section.navigation == "leaf"

    def test_section_ranges_valid(self, doc):
        n = len(doc.lines)
        prev_end = 0
        for s in doc.sections:
            assert s.lines is not None
            start, end = s.lines
            assert 1 <= start <= end <= n
            assert start > prev_end  # contiguous, document order
            prev_end = end

    def test_synthesized_id_for_missing_identifier(self, doc):
        node = doc._node_by_id["/us/pl/99/99/@@s004"]
        assert node.type == "section"
        assert node.number == "SEC. 310"


class TestOutline:
    def test_root_children(self, doc):
        r = doc.root
        assert r.type == "plaw"
        assert r.id == "/us/pl/99/99"
        assert [c.id for c in r.children] == [
            "/us/pl/99/99/s1",
            "/us/pl/99/99/tI",
            "/us/pl/99/99/tII",
        ]
        assert r.navigation == "navigable"
        assert r.child_count == 3
        assert r.section_count == 6
        assert r.lines == [1, len(doc.lines)]

    def test_division_children(self, doc):
        tI = doc._node_by_id["/us/pl/99/99/tI"]
        assert tI.type == "division"
        assert tI.number == "TITLE I"
        assert tI.title == "TEST"
        assert [c.id for c in tI.children] == [
            "/us/pl/99/99/tI/stA",
            "/us/pl/99/99/tI/s201",
        ]
        assert tI.section_count == 3
        assert tI.child_count == 2
        stA = doc._node_by_id["/us/pl/99/99/tI/stA"]
        assert stA.type == "subtitle"
        assert stA.number == "Subtitle A"
        assert stA.section_count == 2
        assert [c.id for c in stA.children] == [
            "/us/pl/99/99/tI/stA/s101",
            "/us/pl/99/99/tI/stA/s102",
        ]
        s201 = doc._node_by_id["/us/pl/99/99/tI/s201"]
        assert s201.parent is tI

    def test_lines_span(self, doc):
        tI = doc._node_by_id["/us/pl/99/99/tI"]
        stA = doc._node_by_id["/us/pl/99/99/tI/stA"]
        # division span covers its subtitle span
        assert tI.lines[0] <= stA.lines[0]
        assert stA.lines[1] <= tI.lines[1]


class TestAssignments:
    def test_toc_excluded_and_boundaries(self, doc):
        assignments = doc.generate_assignments(target_chars=200)
        assert assignments, "expected at least one assignment"
        assert doc.toc_section.lines[0] not in {
            a.start_line for a in assignments
        }
        # every section whose start falls in an assignment fits wholly inside
        for a in assignments:
            for s in doc.sections:
                if s.is_toc:
                    continue
                if a.start_line <= s.lines[0] <= a.end_line:
                    assert s.lines[0] >= a.start_line
                    assert s.lines[1] <= a.end_line, (
                        f"section {s.id} split across {a.id}"
                    )
        # every non-TOC section is covered by exactly one assignment
        for s in doc.sections:
            if s.is_toc:
                continue
            covering = [
                a for a in assignments
                if a.start_line <= s.lines[0] <= a.end_line
            ]
            assert len(covering) == 1, f"section {s.id} covered {len(covering)}x"
        ids = [a.id for a in assignments]
        assert ids == [f"a{i + 1:04d}" for i in range(len(assignments))]

    def test_huge_section_gets_own_assignment(self, doc):
        assignments = doc.generate_assignments()  # default target 15000
        huge = doc._node_by_id["/us/pl/99/99/tII/s301"]
        covering = [
            a for a in assignments if a.start_line <= huge.lines[0] <= a.end_line
        ]
        assert len(covering) == 1
        a = covering[0]
        assert a.start_line <= huge.lines[0] and huge.lines[1] <= a.end_line
        # no other section in that assignment
        others = [
            s for s in doc.sections
            if not s.is_toc and s.id != huge.id
            and a.start_line <= s.lines[0] <= a.end_line
        ]
        assert not others


class TestNavigate:
    def test_root(self, doc):
        r = _nav(doc)
        assert r["node"]["id"] == "/us/pl/99/99"
        assert r["total_children"] == 3
        assert r["cursor"] is None
        ids = [c["id"] for c in r["children"]]
        assert ids == ["/us/pl/99/99/s1", "/us/pl/99/99/tI", "/us/pl/99/99/tII"]
        assert "siblings" not in r
        assert "table-of-contents" in r["note"]
        for c in r["children"]:
            assert c["navigation"] in ("navigable", "leaf")
            assert "lines" in c and "section_count" in c

    def test_division_returns_subtitles_and_sections(self, doc):
        r = _nav(doc, id="/us/pl/99/99/tI")
        assert [c["id"] for c in r["children"]] == [
            "/us/pl/99/99/tI/stA",
            "/us/pl/99/99/tI/s201",
        ]
        assert r["cursor"] is None
        # path breadcrumbs root-first
        assert [p["id"] for p in r["path"]] == ["/us/pl/99/99", "/us/pl/99/99/tI"]
        # siblings: previous = TOC section, next = tII
        sibs = r["siblings"]
        assert sibs[0]["id"] == "/us/pl/99/99/s1"
        assert sibs[1]["id"] == "/us/pl/99/99/tI" and sibs[1]["current"] is True
        assert sibs[2]["id"] == "/us/pl/99/99/tII"

    def test_section_is_leaf_with_siblings(self, doc):
        r = _nav(doc, id="/us/pl/99/99/tI/stA/s102")
        assert r["node"]["navigation"] == "leaf"
        assert r["children"] == []
        assert r["total_children"] == 0
        sibs = r["siblings"]
        assert [s["id"] for s in sibs] == [
            "/us/pl/99/99/tI/stA/s101",
            "/us/pl/99/99/tI/stA/s102",
        ]
        assert sibs[1]["current"] is True
        assert "Leaf node" in r["note"]

    def test_unknown_id(self, doc):
        r = _nav(doc, id="/nope")
        assert "error" in r

    def test_pagination(self, tmp_path):
        secs = "\n".join(
            f"""<section identifier="/us/pl/77/77/tI/stA/s{i:03d}">
                <num>SEC. {i}. </num><heading>SECTION {i}.</heading>
                <content><p>Body {i}.</p></content>
              </section>"""
            for i in range(51)
        )
        xml = f"""<pLaw xmlns="{NS}">
          <main>
            <enactingFormula>Be it enacted.</enactingFormula>
            <title identifier="/us/pl/77/77/tI">
              <num>TITLE I—</num><heading>LOTS</heading>
              <subtitle identifier="/us/pl/77/77/tI/stA">
                <num>Subtitle A—</num><heading>Many</heading>
                {secs}
              </subtitle>
            </title>
          </main>
        </pLaw>"""
        p = tmp_path / "lots.xml"
        p.write_text(xml, encoding="utf-8")
        d = XmlDocument.from_uslm(p)
        st = d._node_by_id["/us/pl/77/77/tI/stA"]
        r1 = _nav(d, id=st.id)
        assert r1["total_children"] == 51
        assert len(r1["children"]) == 50
        assert r1["cursor"] == "50"
        assert "Page 1 of 2" in r1["note"]
        r2 = _nav(d, id=st.id, cursor="50")
        assert len(r2["children"]) == 1
        assert r2["cursor"] is None
        assert r2["children"][0]["id"] == "/us/pl/77/77/tI/stA/s050"


class TestAugmentation:
    def test_search_hit_in_section(self, doc):
        hit = next(
            i + 1 for i, l in enumerate(doc.lines) if "H.R. 1" in l
        )
        extra = doc.augment_search_hit(hit)
        assert 'In: TITLE I (TEST) > Subtitle A (Alpha) > SEC. 101' in extra
        assert 'navigate("/us/pl/99/99/tI/stA/s101")' in extra
        assert "[lines" in extra

    def test_search_hit_front_matter(self, doc):
        extra = doc.augment_search_hit(1)
        assert "front matter" in extra

    def test_search_hit_in_toc(self, doc):
        toc_lines = range(doc.toc_section.lines[0], doc.toc_section.lines[1] + 1)
        extra = doc.augment_search_hit(toc_lines[0])
        assert "SECTION 1" in extra

    def test_task_prompt(self, doc):
        assignments = doc.generate_assignments(target_chars=200)
        a = assignments[1] if len(assignments) > 1 else assignments[0]
        note = doc.augment_task_prompt(a)
        assert note.startswith("Assigned sections:")
        assert "TITLE" in note or "Subtitle" in note
