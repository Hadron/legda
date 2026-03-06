from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from legda.models import Document


class RetrievalPlugin(ABC):
    """Contract for document retrieval providers."""

    @abstractmethod
    async def documents_since(self, since: datetime) -> set[Document]:
        """Return document references published on/after ``since``."""

    @abstractmethod
    async def get_document(self, document: Document) -> bytes:
        """Download a document previously returned by ``documents_since``."""

