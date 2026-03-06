from __future__ import annotations

from datetime import date
from enum import Enum

from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship


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

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    publication_date: Mapped[date] = mapped_column()
    document_id: Mapped[str] = mapped_column(String(64))
    document_type: Mapped[DocumentType] = mapped_column(
        SqlEnum(DocumentType, name="document_type")
    )
    url: Mapped[str] = mapped_column(String(4096))
    filename: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["publication_date", "document_id"],
            ["documents.publication_date", "documents.document_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "publication_date",
            "document_id",
            "document_type",
            "url",
            name="uq_document_instance_identity",
        ),
    )

    document: Mapped[Document] = relationship(back_populates="instances")
