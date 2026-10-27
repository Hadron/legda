"""USLM (GPO) XML support: lossless flattening plus a navigable outline.

``XmlDocument`` parses a USLM 2.0 public-law XML file (e.g.
``PLAW-119publ21_uslm.xml``) and exposes it through the same interface as
``TextDocument``:

- ``lines``: clean, line-wrapped plain text (no markup) with ``[Page N]``
  markers. Every section's heading and body prose is preserved; only markup
  and processing instructions disappear.
- outline: root (plaw) > divisions (``<title>``) > subtitles (``<subtitle>``)
  > sections (``<section>``), navigable via the deterministic ``navigate``
  tool. ``<chapter>``/``<subchapter>``/``<part>`` are pass-through; sections
  attach to their nearest subtitle or division. A section nested inside
  another section is a quoted Code section (an amendment target): it is
  excluded from the outline and assignments, and its text is emitted inline
  within the enclosing bill section. The table-of-contents section (the only
  one whose ``<content>`` directly contains a ``<toc>``) is a top level leaf,
  flagged ``is_toc`` and excluded from assignments.
- ``generate_assignments``: section-aligned — groups consecutive sections into
  assignments of roughly ``target_chars``, breaking only at section boundaries.
- ``augment_search_hit`` / ``augment_task_prompt``: structural context.

Design notes
------------
- Inline/decorative USLM elements (``<ref>``, ``<amendingAction>``,
  ``<sidenote>``, ``<b>``/``<i>``, ``<term>``, ...) are unwrapped so their
  text and citations survive inline.
- Structural "block" elements (``<subsection>``, ``<paragraph>``,
  ``<subparagraph>``, ``<clause>``, ``<subclause>``, ``<item>``, ``<subitem>``,
  ``<level>``, ``<p>``, ``<proviso>``, ``<continuation>``, ``<note>``,
  ``<referenceItem>``) each become one logical line: their ``<num>`` prefix
  (or, for TOC entries, the ``<designator>``) plus all inline text up to the
  next nested block. ``<chapeau>``/``<heading>`` merge into the enclosing
  block's line, matching printed statute format.
- ``<page>`` emits ``[Page N]`` with a sequential counter starting at 1; when
  the page carries a running head (an ``/us/stat/...`` identifier or clean
  text) it is preserved: ``[Page 3 — 139 STAT. 72]``.
- Sections lacking an ``identifier`` attribute get a stable position-based id
  ``<plaw-id>/@@sNNN`` (NNN = 0-based document-order index among all sections).
- ``<meta>`` (machine metadata), ``<endMarker>`` and xhtml ``<col>`` are
  dropped; xhtml table rows (``<tr>``) become one line with cells joined by
  ``" | "``.
"""

from __future__ import annotations

import bisect
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from provision_extraction.text import _append_wrapped
from provision_graph.document import TextDocument
from provision_graph.models import Assignment

USLM_NS = "http://schemas.gpo.gov/xml/uslm"
XHTML_NS = "http://www.w3.org/1999/xhtml"
DC_NS = "http://purl.org/dc/elements/1.1/"

# Structural elements that each become their own logical line.
BLOCK_TAGS = {
    "subsection",
    "paragraph",
    "subparagraph",
    "clause",
    "subclause",
    "item",
    "subitem",
    "level",
    "p",
    "proviso",
    "continuation",
    "note",
    "referenceItem",
}
# Elements whose inner text is inlined verbatim into the current line
# (side headings belong to the body line they decorate).
INLINE_TAGS = {"sidenote"}
# Elements that are never emitted (machine metadata / decoration).
SKIP_TAGS = {"meta", "endMarker", "col"}
# Front-matter containers whose children each get their own line.
FRONT_MATTER_TAGS = {
    "longTitle",
    "preface",
    "enactingFormula",
    "action",
    "legislativeHistory",
}
# Pass-through structural containers in the outline.
WRAPPER_TAGS = {"chapter", "subchapter", "part"}

MAX_CHARS = 120
PAGE_SIZE = 50
DEFAULT_TARGET_CHARS = 15000


def _local(e: ET.Element) -> str:
    return e.tag.rsplit("}", 1)[-1]


def _norm_ws(s: str) -> str:
    s = (s or "").replace("\u00ad", "")  # soft hyphen -> nothing
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class OutlineNode:
    """One node in the navigable outline.

    ``lines`` is the inclusive 1-based prepared-line range the node occupies
    (``[start, end]``); ``None`` for nodes with no text of their own.
    ``section_count`` counts section leaves in the subtree (a section counts
    itself, so leaf sections have ``section_count == 1``).
    """

    id: str
    type: str  # 'plaw' | 'division' | 'subtitle' | 'section'
    number: str
    title: str
    lines: Optional[list[int]]
    navigation: str = "navigable"  # 'navigable' | 'leaf'
    child_count: int = 0
    section_count: int = 0
    is_toc: bool = False
    children: list["OutlineNode"] = field(default_factory=list)
    parent: Optional["OutlineNode"] = None


# ---------------------------------------------------------------------------
# Flattener: USLM element tree -> logical lines, each tagged with a section id
# ---------------------------------------------------------------------------


class _Flattener:
    """Walks the USLM tree in document order and emits tagged logical lines."""

    def __init__(self, plaw_id: str, nested_sections: set | None = None):
        self.plaw_id = plaw_id
        self.page_count = 0
        self.lines: list[tuple[Optional[str], str]] = []
        self.section_info: list[dict] = []
        self.buf = ""
        self.current: Optional[str] = None
        self._section_index = 0
        # <section> elements nested inside another <section> are quoted Code
        # sections (amendment targets); their text is emitted inline within the
        # enclosing bill section, so they are never emitted as their own
        # section.
        self._nested_sections = nested_sections or set()

    # -- line emission ------------------------------------------------------

    def _emit(self, text: str) -> None:
        text = _norm_ws(text)
        if text:
            self.lines.append((self.current, text))

    def _flush(self) -> None:
        if self.buf:
            self._emit(self.buf)
            self.buf = ""

    def _running_head(self, e: ET.Element) -> str:
        ident = e.get("identifier") or ""
        m = re.fullmatch(r"/us/stat/(\d+)/(\d+)", ident)
        if m:
            return f"{m.group(1)} STAT. {m.group(2)}"
        txt = (e.text or "").strip()
        if txt and not txt.startswith("?"):
            return txt
        return ""

    # -- text helpers -------------------------------------------------------

    def _inline_text(self, e: ET.Element) -> str:
        """All text inside ``e`` with nested markup unwrapped, one line.

        Text nodes are concatenated directly (USLM carries the typeset
        spaces in its text nodes) so inline markup does not inject extra
        spaces (e.g. ``[<ref>H.R. 1</ref>]`` -> ``[H.R. 1]``)."""
        parts: list[str] = []
        if e.text:
            parts.append(e.text)
        for c in e:
            parts.append(self._inline_text(c))
            if c.tail:
                parts.append(c.tail)
        return _norm_ws("".join(parts))

    # -- main walk ----------------------------------------------------------

    def flatten(self, root_el: ET.Element) -> tuple[list, list]:
        for child in root_el:
            t = _local(child)
            if t in SKIP_TAGS:
                continue
            if t == "main":
                self._walk_main(child)
            else:
                self.current = None  # preface / legislativeHistory
                self._flush()
                self._walk(child)
        self._flush()
        return self.lines, self.section_info

    def _walk_main(self, main: ET.Element) -> None:
        for child in main:
            t = _local(child)
            if t == "section":
                self._emit_section(child)
            elif t == "title":
                for sec in child.iter(f"{{{USLM_NS}}}section"):
                    if sec in self._nested_sections:
                        # Quoted Code section: already emitted inline within
                        # its enclosing bill section's content.
                        continue
                    self._emit_section(sec)
            else:
                self.current = None  # longTitle / enactingFormula / action
                self._flush()
                self._walk(child)

    def _walk(self, e: ET.Element) -> None:
        """Append ``e``'s content (in document order) to the current line
        buffer, emitting block elements as separate lines."""
        t = _local(e)
        if t in SKIP_TAGS:
            return
        if t == "page":
            self._flush()
            self.page_count += 1
            marker = f"[Page {self.page_count}]"
            run = self._running_head(e)
            if run:
                marker += f" — {run}"
            self.lines.append((self.current, marker))
            return
        if t == "tr":  # xhtml table row -> one line, cells joined
            self._flush()
            cells = [
                self._inline_text(c).strip()
                for c in e
                if _local(c) in ("th", "td")
            ]
            cells = [c for c in cells if c]
            if cells:
                self._emit(" | ".join(cells))
            return
        if t in INLINE_TAGS:
            parts = [self._inline_text(c) for c in e]
            s = " ".join(p for p in parts if p)
            if s.strip():
                self.buf += (" " if self.buf else "") + s.strip() + " "
            return
        if t in BLOCK_TAGS:
            self._flush()
            prefix, skip = self._block_prefix(e, t)
            self.buf = prefix + (e.text or "")
            for c in e:
                if c in skip:
                    continue
                self._walk(c)
                if c.tail:
                    self.buf += c.tail
            return
        # front-matter container: each child gets its own line
        if t in FRONT_MATTER_TAGS:
            if e.text:
                self.buf += e.text
            for c in e:
                self._walk(c)
                if c.tail:
                    self.buf += c.tail
                self._flush()
            return
        # container / inline element
        if e.text:
            self.buf += e.text
        for c in e:
            self._walk(c)
            if c.tail:
                self.buf += c.tail

    def _block_prefix(
        self, e: ET.Element, t: str
    ) -> tuple[str, set]:
        """Prefix for a block line: its <num> (or <designator> for TOC
        entries), plus the prefix elements to skip when walking children."""
        if t == "referenceItem":
            d = e.find(f"{{{USLM_NS}}}designator")
            if d is not None:
                return self._inline_text(d).strip() + " ", {d}
            return "", set()
        for c in e:
            if _local(c) == "num":
                return self._inline_text(c).strip() + " ", {c}
        return "", set()

    # -- sections -----------------------------------------------------------

    def _emit_section(self, e: ET.Element) -> None:
        ident = e.get("identifier")
        if not ident:
            ident = f"{self.plaw_id}/@@s{self._section_index:03d}"
        self._section_index += 1
        num_el = e.find(f"{{{USLM_NS}}}num")
        heading_el = e.find(f"{{{USLM_NS}}}heading")
        num = self._inline_text(num_el) if num_el is not None else ""
        heading = self._inline_text(heading_el) if heading_el is not None else ""
        heading_line = _norm_ws(f"{num} {heading}")
        self.current = ident
        if self.lines:
            self.lines.append((ident, ""))  # blank line before each section
        if heading_line:
            self.lines.append((ident, heading_line))
        for c in e:
            if c is num_el or c is heading_el:
                continue
            self._walk(c)
            if c.tail:
                self.buf += c.tail
        self._flush()
        self.section_info.append(
            {
                "id": ident,
                "num": num,
                "heading": heading,
                "toc": self._is_toc(e),
                "el": e,
            }
        )

    @staticmethod
    def _is_toc(e: ET.Element) -> bool:
        """True for the only section whose <content> directly contains <toc>."""
        for c in e:
            if _local(c) == "content":
                return any(_local(x) == "toc" for x in c)
        return any(_local(x) == "toc" for x in e)


# ---------------------------------------------------------------------------
# Logical lines -> prepared lines (wrap + collapse), keeping the section tag
# ---------------------------------------------------------------------------


def _prepare_logical(
    logical: list[tuple[Optional[str], str]]
) -> tuple[list[str], list[Optional[str]]]:
    out_text: list[str] = []
    out_sec: list[Optional[str]] = []
    for sec_id, text in logical:
        before = len(out_text)
        if not text.strip():
            out_text.append("")
            out_sec.append(sec_id)
        else:
            _append_wrapped(out_text, text, MAX_CHARS)
            out_sec.extend([sec_id] * (len(out_text) - before))
    return _collapse_blanks_pair(out_text, out_sec)


def _collapse_blanks_pair(
    text: list[str], sec: list[Optional[str]]
) -> tuple[list[str], list[Optional[str]]]:
    """Mirror of ``text._collapse_blanks`` that carries the section tag."""
    res_t: list[str] = []
    res_s: list[Optional[str]] = []
    prev_blank = False
    for t, s in zip(text, sec):
        if t == "":
            if not prev_blank and res_t:
                res_t.append("")
                res_s.append(s)
                prev_blank = True
        else:
            res_t.append(t)
            res_s.append(s)
            prev_blank = False
    while res_t and res_t[0] == "":
        res_t.pop(0)
        res_s.pop(0)
    while res_t and res_t[-1] == "":
        res_t.pop()
        res_s.pop()
    return res_t, res_s


# ---------------------------------------------------------------------------
# Outline helpers
# ---------------------------------------------------------------------------


def _derive_plaw_id(sections_el: list[ET.Element]) -> str:
    for s in sections_el:
        ident = s.get("identifier") or ""
        parts = ident.split("/")
        if len(parts) >= 5 and parts[1:3] == ["us", "pl"]:
            return "/".join(parts[:5])
    return "/us/pl/unknown"


def _el_text(e: ET.Element, tag: str) -> str:
    child = e.find(f"{{{USLM_NS}}}{tag}")
    if child is None:
        return ""
    return _norm_ws(" ".join(child.itertext()))


def _norm_num(raw: str, kind: str) -> str:
    s = _norm_ws(raw)
    if s.startswith(("“", "‘", '"', "'")):  # inserted-content quote mark
        s = s[1:].strip()
    if kind in ("division", "subtitle"):
        s = s.rstrip("—-").strip()
    elif kind == "section" and s.endswith("."):
        s = s[:-1].strip()
    return s


def _norm_heading(raw: str) -> str:
    s = _norm_ws(raw)
    if s.endswith("."):
        s = s[:-1].strip()
    return s


def _section_ranges(
    line_sections: list[Optional[str]], ids: list[str]
) -> dict[str, list[int]]:
    ranges: dict[str, list[int]] = {}
    for i, s in enumerate(line_sections, start=1):
        if s is None:
            continue
        r = ranges.setdefault(s, [i, i])
        r[1] = i
    missing = [i for i in ids if i not in ranges]
    if missing:
        raise ValueError(
            f"{len(missing)} section(s) produced no prepared lines: {missing[:5]}"
        )
    return ranges


# ---------------------------------------------------------------------------
# XmlDocument
# ---------------------------------------------------------------------------


class XmlDocument(TextDocument):
    """A USLM public-law XML document with a navigable section outline."""

    def __init__(
        self,
        lines: list[str],
        *,
        plaw_id: str,
        root: OutlineNode,
        sections: list[OutlineNode],
        toc_section: Optional[OutlineNode],
        line_sections: list[Optional[str]],
        node_by_id: dict[str, OutlineNode],
    ):
        super().__init__(lines)
        self.plaw_id = plaw_id
        self.root = root
        self.sections = sections  # all sections, document order (incl. TOC)
        self.toc_section = toc_section
        self.line_sections = line_sections
        self._node_by_id = node_by_id

    # -- construction -------------------------------------------------------

    @classmethod
    def from_uslm(cls, path: str | Path) -> "XmlDocument":
        """Parse a USLM 2.0 XML file, flatten it, and build the outline."""
        path = Path(path)
        tree = ET.parse(str(path))
        root_el = tree.getroot()
        sections_el = list(root_el.iter(f"{{{USLM_NS}}}section"))
        if not sections_el:
            raise ValueError(f"No <section> elements found in {path}")

        plaw_id = _derive_plaw_id(sections_el)
        plaw_number, plaw_title = _meta_identity(root_el, plaw_id)

        # A bill section is a <section> that is NOT nested inside another
        # <section>. Nested sections are quoted Code sections (the target of an
        # amendment); their text is emitted inline within the enclosing bill
        # section, so they are excluded from the outline and assignments.
        parent_map = {c: p for p in root_el.iter() for c in p}

        def _has_section_ancestor(el) -> bool:
            cur = el
            while cur in parent_map:
                cur = parent_map[cur]
                if cur.tag == f"{{{USLM_NS}}}section":
                    return True
            return False

        nested_sections = {s for s in sections_el if _has_section_ancestor(s)}

        fl = _Flattener(plaw_id, nested_sections)
        logical, section_info = fl.flatten(root_el)
        lines, line_sections = _prepare_logical(logical)
        ranges = _section_ranges(
            line_sections, [si["id"] for si in section_info]
        )

        root_node = OutlineNode(
            id=plaw_id,
            type="plaw",
            number=plaw_number,
            title=plaw_title,
            lines=[1, len(lines)],
            navigation="navigable",
        )
        node_by_el: dict[ET.Element, OutlineNode] = {}
        for si in section_info:
            node = OutlineNode(
                id=si["id"],
                type="section",
                number=_norm_num(si["num"], "section"),
                title=_norm_heading(si["heading"]),
                lines=ranges[si["id"]],
                navigation="leaf",
                section_count=1,
                is_toc=si["toc"],
            )
            node.parent = root_node
            node_by_el[si["el"]] = node

        toc_node: Optional[OutlineNode] = None
        for node in node_by_el.values():
            if node.is_toc:
                toc_node = node
                break

        # Attach every division/subtitle/section to its nearest navigable
        # ancestor (section -> subtitle -> division -> root). Sections quoted
        # inside another section's body nest under the same subtitle/division
        # as their enclosing section. Children keep document order.
        main = root_el.find(f"{{{USLM_NS}}}main")
        parent_map = {c: p for p in root_el.iter() for c in p}
        order = {id(e): i for i, e in enumerate(root_el.iter())}
        el_to_node: dict = {}
        children: dict[int, tuple[OutlineNode, list[tuple[int, OutlineNode]]]] = {}

        def add_child(parent_node: OutlineNode, child_node: OutlineNode, el) -> None:
            children.setdefault(id(parent_node), (parent_node, []))[1].append(
                (order[id(el)], child_node)
            )

        def nearest(el, wanted: set) -> Optional[OutlineNode]:
            anc = parent_map.get(el)
            while anc is not None and _local(anc) != "main":
                if _local(anc) in wanted:
                    return el_to_node.get(anc)
                anc = parent_map.get(anc)
            return None

        if main is not None:
            for title_el in main.findall(f"{{{USLM_NS}}}title"):
                dnode = OutlineNode(
                    id=title_el.get("identifier")
                    or f"{plaw_id}/@@t{len(el_to_node) + 1}",
                    type="division",
                    number=_norm_num(_el_text(title_el, "num"), "division"),
                    title=_norm_heading(_el_text(title_el, "heading")),
                    lines=None,
                    navigation="navigable",
                )
                dnode.parent = root_node
                el_to_node[title_el] = dnode
                add_child(root_node, dnode, title_el)

        for sub_el in root_el.iter(f"{{{USLM_NS}}}subtitle"):
            snode = OutlineNode(
                id=sub_el.get("identifier") or f"{plaw_id}/@@st{len(el_to_node) + 1}",
                type="subtitle",
                number=_norm_num(_el_text(sub_el, "num"), "subtitle"),
                title=_norm_heading(_el_text(sub_el, "heading")),
                lines=None,
                navigation="navigable",
            )
            el_to_node[sub_el] = snode
            target = nearest(sub_el, {"title"}) or root_node
            snode.parent = target
            add_child(target, snode, sub_el)

        for sec_el, node in node_by_el.items():
            target = nearest(sec_el, {"title", "subtitle"}) or root_node
            node.parent = target
            add_child(target, node, sec_el)

        for parent_node, pairs in children.values():
            pairs.sort(key=lambda p: p[0])
            parent_node.children.extend(n for _, n in pairs)

        _finalize(root_node)

        node_by_id: dict[str, OutlineNode] = {}

        def _index(node: OutlineNode) -> None:
            node_by_id[node.id] = node
            for c in node.children:
                _index(c)

        _index(root_node)

        return cls(
            lines,
            plaw_id=plaw_id,
            root=root_node,
            sections=[node_by_el[si["el"]] for si in section_info],
            toc_section=toc_node,
            line_sections=line_sections,
            node_by_id=node_by_id,
        )

    # -- navigation helpers ---------------------------------------------------

    def _path_to(self, node: OutlineNode) -> list[OutlineNode]:
        chain = [node]
        while chain[-1].parent is not None:
            chain.append(chain[-1].parent)
        return list(reversed(chain))

    def _section_at(self, line: int) -> Optional[OutlineNode]:
        starts = [s.lines[0] for s in self.sections]
        i = bisect.bisect_right(starts, line) - 1
        if i >= 0:
            s = self.sections[i]
            if s.lines[0] <= line <= s.lines[1]:
                return s
        return None

    # -- TextDocument interface ----------------------------------------------

    def generate_assignments(
        self, target_chars: int = DEFAULT_TARGET_CHARS
    ) -> list[Assignment]:
        """Section-aligned assignments: break only at section boundaries,
        excluding the TOC section. A section larger than ``target_chars``
        gets its own assignment."""
        groups: list[list[OutlineNode]] = []
        cur: list[OutlineNode] = []
        used = 0
        for s in self.sections:
            if s.is_toc:
                continue
            size = sum(
                len(l) + 1 for l in self.lines[s.lines[0] - 1 : s.lines[1]]
            )
            if cur and used + size > target_chars:
                groups.append(cur)
                cur = []
                used = 0
            cur.append(s)
            used += size
        if cur:
            groups.append(cur)
        return [
            Assignment(
                id=f"a{i + 1:04d}",
                start_line=g[0].lines[0],
                end_line=g[-1].lines[1],
            )
            for i, g in enumerate(groups)
        ]

    def optional_tools(self) -> list:
        doc = self

        @tool
        def navigate(id: str = "", cursor: str = "") -> str:
            """Navigate this law's outline (divisions > subtitles > sections).
            Call with no arguments for the root (table of contents + divisions);
            then pass a node's id to drill down. More than 50 children are
            paginated: pass the returned cursor to see the next page. Sections
            are leaf nodes; read a section's text with retrieve(start, end)
            using its lines."""
            return doc._navigate(id or "", cursor or "")

        return [navigate]

    def _node_brief(self, node: OutlineNode, with_lines: bool = False) -> dict:
        d: dict = {
            "id": node.id,
            "type": node.type,
            "number": node.number,
            "title": node.title,
        }
        if with_lines:
            d["lines"] = node.lines
            d["child_count"] = node.child_count
        d["section_count"] = node.section_count
        if with_lines:
            d["navigation"] = node.navigation
        if node.is_toc:
            d["toc"] = True
        return d

    def _navigate(self, id: str, cursor: str) -> str:
        node = self._node_by_id.get(id) if id else self.root
        if node is None:
            return json.dumps({"error": f"No node with id {id!r}."})
        off = int(cursor) if cursor.strip().isdigit() else 0
        children = node.children
        off = max(0, min(off, len(children)))
        page = children[off : off + PAGE_SIZE]
        next_cursor = (
            str(off + PAGE_SIZE) if off + PAGE_SIZE < len(children) else None
        )

        result: dict = {
            "node": self._node_brief(node, with_lines=True),
            "path": [self._node_brief(a) for a in self._path_to(node)],
            "children": [self._node_brief(c, with_lines=True) for c in page],
            "total_children": len(children),
            "cursor": next_cursor,
        }
        if node.parent is not None:
            idx = node.parent.children.index(node)
            sibs = []
            if idx > 0:
                sibs.append(self._node_brief(node.parent.children[idx - 1]))
            cur = self._node_brief(node)
            cur["current"] = True
            sibs.append(cur)
            if idx + 1 < len(node.parent.children):
                sibs.append(self._node_brief(node.parent.children[idx + 1]))
            result["siblings"] = sibs

        if not children:
            note = (
                "Leaf node: no children. Read its text with "
                "retrieve(start, end) using its lines."
            )
        elif next_cursor is None:
            note = f"All {len(children)} children shown (complete)."
            if node.type == "plaw":
                note += (
                    " The table-of-contents section and the divisions are "
                    "listed; navigate into a division by id."
                )
            elif node.type == "division":
                note += (
                    " Navigate into a subtitle (or a direct section) by id."
                )
            note += (
                " Sections are leaf nodes: read their text with "
                "retrieve(start, end)."
            )
        else:
            pages = (len(children) + PAGE_SIZE - 1) // PAGE_SIZE
            note = (
                f"Page {off // PAGE_SIZE + 1} of {pages} — "
                f"{len(page)} of {len(children)} children; "
                f"navigate(id='{node.id}', cursor='{next_cursor}') for more."
            )
        result["note"] = note
        return json.dumps(result, ensure_ascii=False)

    def augment_search_hit(self, line: int) -> str:
        node = self._section_at(line)
        if node is None:
            return "In: front matter (not inside any section)"
        parts = []
        for a in self._path_to(node):
            if a is self.root:
                continue
            if a.type == "section":
                head = f"{a.number}"
                if a.title:
                    parts.append(f'{head} "{_truncate(a.title)}"')
                else:
                    parts.append(head)
            else:
                parts.append(f"{a.number} ({a.title})")
        head = "In: " + " > ".join(parts)
        span = f"[lines {node.lines[0]}-{node.lines[1]}]"
        return (
            f"{head} {span}\n"
            f'navigate("{node.id}") for neighboring sections'
        )

    def augment_task_prompt(self, assignment: Assignment) -> str:
        labels: list[str] = []
        for s in self.sections:
            if s.is_toc:
                continue
            if assignment.start_line <= s.lines[0] <= assignment.end_line:
                p = s.parent
                if p is None or p.type == "plaw":
                    label = s.number
                else:
                    label = f"{p.number} ({p.title})"
                    gp = p.parent
                    if gp is not None and gp.type == "division":
                        label = f"{gp.number} ({gp.title}) > {label}"
                if label not in labels:
                    labels.append(label)
        if not labels:
            return ""
        shown = labels[:6]
        extra = f" (+{len(labels) - 6} more)" if len(labels) > 6 else ""
        return "Assigned sections: " + "; ".join(shown) + extra


def _truncate(s: str, n: int = 50) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def _finalize(node: OutlineNode) -> None:
    for c in node.children:
        _finalize(c)
    node.child_count = len(node.children)
    if node.type == "section":
        node.section_count = 1
        return
    node.section_count = sum(c.section_count for c in node.children)
    if node.type in ("division", "subtitle"):
        spans = [c.lines for c in node.children if c.lines]
        if spans:
            node.lines = [min(s[0] for s in spans), max(s[1] for s in spans)]


def _meta_identity(root_el: ET.Element, plaw_id: str) -> tuple[str, str]:
    meta = root_el.find(f"{{{USLM_NS}}}meta")
    number, title = plaw_id, ""
    if meta is not None:
        ca = meta.find(f"{{{USLM_NS}}}citableAs")
        if ca is not None and _norm_ws(ca.text):
            number = _norm_ws(ca.text)
        dt = meta.find(f"{{{DC_NS}}}title")
        if dt is not None and _norm_ws(dt.text):
            title = _norm_ws(dt.text)
    return number, title
