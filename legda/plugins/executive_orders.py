from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from legda.models import Document
from legda.plugins.base import RetrievalPlugin


class ExecutiveOrderRetriever(RetrievalPlugin):
    """Retrieve executive orders from the Federal Register API."""

    api_base_url = "https://www.federalregister.gov/api/v1/documents.json"
    default_per_page = 100
    timeout_seconds = 30.0

    async def documents_since(self, since: datetime) -> set[Document]:
        since_date = self._date_filter(since)
        page = 1
        documents: set[Document] = set()

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            while True:
                payload = await self._fetch_json(
                    client,
                    {
                        "conditions[presidential_document_type][]": "executive_order",
                        "conditions[publication_date][gte]": since_date,
                        "order": "oldest",
                        "page": page,
                        "per_page": self.default_per_page,
                    },
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
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(download_url)
            response.raise_for_status()
            return response.content

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

    async def _fetch_json(
        self, client: httpx.AsyncClient, params: dict[str, Any]
    ) -> dict[str, Any]:
        response = await client.get(self.api_base_url, params=params)
        response.raise_for_status()
        return response.json()
