from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import ClassVar

from legda.models import Document
from legda.models import DocumentInstance
from legda.models import DocumentType


class RetrievalPlugin(ABC):
    """Contract for document retrieval providers."""

    plugin_key: ClassVar[str] = "base"
    document_type: ClassVar[str] = "documents"
    preferred_document_types: ClassVar[tuple[DocumentType, ...]] = (
        DocumentType.PDF,
        DocumentType.HTML,
        DocumentType.XML,
    )

    def __init__(self, data_directory: Path | str = "data") -> None:
        self.data_directory = Path(data_directory)

    @abstractmethod
    async def get_documents(self, since: datetime) -> list[Document]:
        """Return discovered documents published on/after ``since``."""

    @abstractmethod
    async def get_document(self, document: Document, instance: DocumentInstance) -> bytes:
        """Download a specific document instance."""

    async def download_document(self, document: Document) -> list[DocumentInstance]:
        """Download selected instances and set each downloaded filename."""
        downloaded: list[DocumentInstance] = []
        for instance in self.instances_to_download(document):
            payload = await self.get_document(document, instance)
            output_path = self._document_output_path(document, instance)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(payload)
            instance.filename = str(output_path)
            downloaded.append(instance)
        return downloaded

    def instances_to_download(self, document: Document) -> list[DocumentInstance]:
        """Select which instances should be downloaded for this plugin."""
        available = [instance for instance in document.instances if not instance.filename]
        if not available:
            return []

        for preferred in self.preferred_document_types:
            for instance in available:
                if instance.document_type == preferred:
                    return [instance]

        return [available[0]]

    def _document_output_path(
        self, document: Document, instance: DocumentInstance
    ) -> Path:
        extension = instance.document_type.value
        filename = (
            f"{document.publication_date.isoformat()}-"
            f"{document.document_id}.{extension}"
        )
        return self.data_directory / self.document_type / filename

