from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Document:
    """A retrievable government document reference."""

    document_number: str
    title: str
    publication_date: date
    html_url: str
    pdf_url: str | None = None
    abstract: str | None = None

