from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from legda.models import Document
from legda.plugins.base import RetrievalPlugin


class ExecutiveOrderRetriever(RetrievalPlugin):
    """Retrieve executive orders from the Federal Register API."""

    api_base_url = "https://www.federalregister.gov/api/v1/documents.json"
    default_per_page = 100

    async def documents_since(self, since: datetime) -> set[Document]:
        since_date = self._date_filter(since)
        page = 1
        documents: set[Document] = set()

        while True:
            payload = await self._fetch_json(
                {
                    "conditions[presidential_document_type][]": "executive_order",
                    "conditions[publication_date][gte]": since_date,
                    "order": "oldest",
                    "page": page,
                    "per_page": self.default_per_page,
                }
            )

            for result in payload.get("results", []):
                documents.add(self._to_document(result))

            next_page_url = payload.get("next_page_url")
            if not next_page_url:
                break

            page += 1

        return documents

    async def get_document(self, document: Document) -> bytes:
        download_url = document.pdf_url or document.html_url
        return await self._fetch_bytes(download_url)

    def _date_filter(self, value: datetime) -> str:
        if value.tzinfo is None:
            normalized = value
        else:
            normalized = value.astimezone(timezone.utc).replace(tzinfo=None)
        return normalized.date().isoformat()

    def _to_document(self, result: dict[str, Any]) -> Document:
        return Document(
            document_number=result["document_number"],
            title=result["title"],
            publication_date=datetime.strptime(
                result["publication_date"], "%Y-%m-%d"
            ).date(),
            html_url=result["html_url"],
            pdf_url=result.get("pdf_url"),
            abstract=result.get("abstract"),
        )

    async def _fetch_json(self, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode(params, doseq=True)
        url = f"{self.api_base_url}?{query}"
        payload = await self._fetch_bytes(url)
        return json.loads(payload.decode("utf-8"))

    async def _fetch_bytes(self, url: str) -> bytes:
        def _download() -> bytes:
            with urlopen(url, timeout=30) as response:  # nosec B310
                return response.read()

        return await asyncio.to_thread(_download)

