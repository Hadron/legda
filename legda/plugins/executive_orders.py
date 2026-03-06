from __future__ import annotations

from datetime import (
    date,
    datetime,
    timezone,
)
from typing import Any

import httpx

from legda.models import (
    Document,
    DocumentInstance,
    DocumentStatus,
    DocumentType,
)
from legda.plugins.base import RetrievalPlugin


class ExecutiveOrderRetriever(RetrievalPlugin):
    """Retrieve executive orders from the Federal Register API."""

    plugin_key = "executive_orders"
    document_type = "executive_orders"
    preferred_document_types = (DocumentType.PDF, DocumentType.HTML)
    api_base_url = "https://www.federalregister.gov/api/v1/documents.json"
    default_per_page = 100
    timeout_seconds = 30.0

    async def get_documents(self, since: datetime) -> list[Document]:
        since_date = self._date_filter(since)
        page = 1
        documents: dict[tuple[date, str], Document] = {}

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
                    publication_date = datetime.strptime(
                        result["publication_date"], "%Y-%m-%d"
                    ).date()
                    document_id = result["document_number"]
                    key = (publication_date, document_id)
                    document = documents.get(key)
                    if document is None:
                        document = Document(
                            publication_date=publication_date,
                            document_id=document_id,
                            plugin=self.plugin_key,
                            title=result["title"],
                            status=DocumentStatus.TO_FETCH,
                        )
                        documents[key] = document

                    self._add_instances(document, result)

                if not payload.get("next_page_url"):
                    break

                page += 1

        return list(documents.values())

    async def get_document(self, document: Document, instance: DocumentInstance) -> bytes:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(instance.url)
            response.raise_for_status()
            return response.content

    def _add_instances(self, document: Document, result: dict[str, Any]) -> None:
        existing = {(instance.document_type, instance.url) for instance in document.instances}

        candidates = (
            (DocumentType.PDF, result.get("pdf_url")),
            (DocumentType.HTML, result.get("html_url")),
            (DocumentType.XML, result.get("full_text_xml_url")),
        )
        for doc_type, url in candidates:
            if not url:
                continue
            key = (doc_type, url)
            if key in existing:
                continue
            document.instances.append(
                DocumentInstance(
                    publication_date=document.publication_date,
                    document_id=document.document_id,
                    document_type=doc_type,
                    url=url,
                    filename=None,
                )
            )
            existing.add(key)

    def _date_filter(self, value: datetime) -> str:
        if value.tzinfo is None:
            normalized = value
        else:
            normalized = value.astimezone(timezone.utc).replace(tzinfo=None)
        return normalized.date().isoformat()

    async def _fetch_json(
        self, client: httpx.AsyncClient, params: dict[str, Any]
    ) -> dict[str, Any]:
        response = await client.get(self.api_base_url, params=params)
        response.raise_for_status()
        return response.json()
