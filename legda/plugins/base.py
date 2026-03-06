from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from legda.models import Document


class RetrievalPlugin(ABC):
    """Contract for document retrieval providers."""

    document_type: ClassVar[str] = "documents"

    def __init__(self, data_directory: Path | str = "data") -> None:
        self.data_directory = Path(data_directory)

    @abstractmethod
    async def documents_since(self, since: datetime) -> set[Document]:
        """Return document references published on/after ``since``."""

    @abstractmethod
    async def get_document(self, document: Document) -> bytes:
        """Download a document previously returned by ``documents_since``."""

    async def download_document(self, document: Document) -> Path:
        """Download and persist a document in the plugin's data directory."""
        payload = await self.get_document(document)
        output_path = self._document_output_path(document)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)
        return output_path

    def _document_output_path(self, document: Document) -> Path:
        extension = self._document_extension(document)
        filename = (
            f"{document.publication_date.isoformat()}-{document.document_number}.{extension}"
        )
        return self.data_directory / self.document_type / filename

    def _document_extension(self, document: Document) -> str:
        pdf_extension = self._extension_from_url(document.pdf_url)
        if pdf_extension:
            return pdf_extension

        html_extension = self._extension_from_url(document.html_url)
        if html_extension:
            return html_extension

        return "html"

    @staticmethod
    def _extension_from_url(url: str | None) -> str | None:
        if not url:
            return None
        suffix = Path(urlparse(url).path).suffix.lstrip(".").lower()
        return suffix or None
