from __future__ import annotations

from datetime import (
    date,
    datetime,
)
from enum import Enum

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKeyConstraint,
    String,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(AsyncAttrs, DeclarativeBase):
    """SQLAlchemy declarative base for the legda data model."""


class DocumentStatus(str, Enum):
    """Lifecycle status for a document record."""

    TO_FETCH = "to_fetch"
    FETCHING = "fetching"
    FETCHED = "fetched"
    FAILED = "failed"


class DocumentType(str, Enum):
    """Known download formats for a document instance."""

    PDF = "pdf"
    HTML = "html"
    XML = "xml"


class Document(Base):
    """Canonical persistent representation of a government document."""

    __tablename__ = "documents"

    publication_date: Mapped[date] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plugin: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(2048))
    status: Mapped[DocumentStatus] = mapped_column(
        SqlEnum(DocumentStatus, name="document_status"),
        default=DocumentStatus.TO_FETCH,
    )

    instances: Mapped[list["DocumentInstance"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
    )


class DocumentInstance(Base):
    """A downloadable representation of a document in a specific format."""

    __tablename__ = "document_instances"

    publication_date: Mapped[date] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_type: Mapped[DocumentType] = mapped_column(
        SqlEnum(DocumentType, name="document_type"),
        primary_key=True,
    )
    url: Mapped[str] = mapped_column(String(4096), primary_key=True)
    filename: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["publication_date", "document_id"],
            ["documents.publication_date", "documents.document_id"],
            ondelete="CASCADE",
        ),
    )

    document: Mapped[Document] = relationship(back_populates="instances")


class FetchDate(Base):
    """Most recent successful discovery timestamp for each plugin."""

    __tablename__ = "fetch_dates"

    plugin: Mapped[str] = mapped_column(String(128), primary_key=True)
    date_fetched: Mapped[datetime] = mapped_column(DateTime(timezone=True))
