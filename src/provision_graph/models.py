"""Data models shared by the graph and the portable extraction logic.

These models are the contract between the portable extraction package and the
graph integration layer. They are expected to be reconciled with existing IRS
models, so keep them plain Pydantic (no LangGraph/LangChain coupling).

Line-based locations: a "location" in the source document is a 1-based line
number in the prepared display lines. A range is inclusive on both ends.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LineRange(BaseModel):
    """An inclusive range of 1-based line numbers in the prepared document."""

    start: int = Field(
        ge=1,
        description="First line number (1-based, inclusive).",
    )
    end: int = Field(
        ge=1,
        description="Last line number (1-based, inclusive).",
    )

    def overlaps(self, other: "LineRange") -> bool:
        return self.start <= other.end and other.start <= self.end


class Citation(BaseModel):
    """A statutory or regulatory citation with an optional change note."""

    cite: str = Field(
        description=(
            "Canonical citation, e.g. 'IRC § 6511(a)' or '22 U.S.C. § 1741b'."
        )
    )
    note: str | None = Field(
        default=None,
        description=(
            "Optional short description of the change, e.g. 'subsection (a) "
            "added', 'fully replaced', 'removed'."
        ),
    )


class Provision(BaseModel):
    """One independently analyzable provision extracted from the document."""

    title: str = Field(description="Short descriptive title for the provision.")
    section_citation: str = Field(
        description=(
            "Human-readable citation to the incoming document, e.g. "
            "'Sec. 101(a)-(c)'. This is the document's own section, not the "
            "amended code section."
        )
    )
    summary: str = Field(
        description="What the provision does and its legal effect."
    )
    effective_date: str = Field(
        description=(
            "The effective/applicability rule as stated, including transitions "
            "or exceptions. If supplied elsewhere in the document, say so. "
            "Use 'Not stated' if the document gives no rule."
        )
    )
    effective_date_evidence: list[LineRange] = Field(
        default_factory=list,
        description="Line ranges supplying the effective date rule.",
    )
    irc_sections_amended: list[Citation] = Field(
        default_factory=list,
        description=(
            "IRC sections amended, added, removed, repealed, or replaced. "
            "Added/removed/repealed all count here."
        ),
    )
    irc_sections_cited: list[Citation] = Field(
        default_factory=list,
        description="IRC sections referenced but not the amendment target.",
    )
    other_sections_amended: list[Citation] = Field(
        default_factory=list,
        description="Non-IRC statutory/regulatory sections amended.",
    )
    other_sections_cited: list[Citation] = Field(
        default_factory=list,
        description="Non-IRC statutory/regulatory sections cited.",
    )
    operative_locations: list[LineRange] = Field(
        default_factory=list,
        description=(
            "Line ranges expressing the rule or change summarized by this "
            "provision. Use multiple ranges when separated."
        ),
    )
    supporting_locations: list[LineRange] = Field(
        default_factory=list,
        description=(
            "Line ranges of definitions, dates, or other passages used to "
            "interpret the rule."
        ),
    )
    assignment_id: str | None = Field(
        default=None,
        description="Provenance: which assignment produced this candidate.",
    )


class Assignment(BaseModel):
    """A contiguous block of prepared lines assigned to one worker."""

    id: str
    start_line: int
    end_line: int


AssignmentStatus = Literal["completed", "failed"]


class AssignmentResult(BaseModel):
    """The outcome of running one assignment through the extraction worker."""

    assignment_id: str
    status: AssignmentStatus
    error: str | None = None
    provisions: list[Provision] = Field(default_factory=list)

    @classmethod
    def failed(cls, assignment_id: str, error: str) -> "AssignmentResult":
        return cls(assignment_id=assignment_id, status="failed", error=error)

    @classmethod
    def completed(
        cls, assignment_id: str, provisions: list[Provision]
    ) -> "AssignmentResult":
        for p in provisions:
            p.assignment_id = assignment_id
        return cls(
            assignment_id=assignment_id, status="completed", provisions=provisions
        )
