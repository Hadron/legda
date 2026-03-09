from __future__ import annotations

import os
from datetime import (
    date,
    datetime,
    timezone,
)
from typing import Any

import httpx
from dotenv import load_dotenv

from legda.models import (
    Document,
    DocumentInstance,
    DocumentStatus,
    DocumentType,
)
from legda.plugins.base import RetrievalPlugin


class CongressRetriever(RetrievalPlugin):
    """Retrieve passed U.S. legislation from Congress.gov and download XML bill text."""

    plugin_key = "congress"
    document_type = "congress"
    preferred_document_types = (DocumentType.XML,)
    api_base_url = "https://api.congress.gov/v3"
    timeout_seconds = 30.0
    page_limit = 250

    async def get_documents(self, since: datetime) -> list[Document]:
        api_key = self._api_key()
        since_date = self._cutoff_date(since)
        start_congress = self._congress_for_date(since_date)
        end_congress = self._congress_for_date(datetime.now(timezone.utc).date())
        documents: dict[tuple[date, str], Document] = {}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for congress in range(start_congress, end_congress + 1):
                offset = 0
                while True:
                    payload = await self._fetch_json(
                        client=client,
                        path=f"/law/{congress}",
                        params={
                            "api_key": api_key,
                            "format": "json",
                            "limit": self.page_limit,
                            "offset": offset,
                        },
                    )
                    laws = payload.get("bills", [])
                    if not laws:
                        break

                    for bill in laws:
                        action_date = self._action_date(bill)
                        if action_date is None or action_date < since_date:
                            continue

                        congress_value = str(bill.get("congress", "")).strip()
                        bill_type = str(bill.get("type", "")).lower().strip()
                        bill_number = str(bill.get("number", "")).strip()
                        if not congress_value or not bill_type or not bill_number:
                            continue

                        xml_url = await self._bill_xml_url(
                            client=client,
                            api_key=api_key,
                            congress=congress_value,
                            bill_type=bill_type,
                            bill_number=bill_number,
                        )
                        if not xml_url:
                            continue

                        document_id = f"{congress_value}-{bill_type}-{bill_number}"
                        key = (action_date, document_id)
                        document = documents.get(key)
                        if document is None:
                            document = Document(
                                publication_date=action_date,
                                document_id=document_id,
                                plugin=self.plugin_key,
                                title=str(bill.get("title", document_id)),
                                status=DocumentStatus.TO_FETCH,
                            )
                            documents[key] = document

                        if not any(
                            instance.document_type == DocumentType.XML
                            and instance.url == xml_url
                            for instance in document.instances
                        ):
                            document.instances.append(
                                DocumentInstance(
                                    publication_date=document.publication_date,
                                    document_id=document.document_id,
                                    document_type=DocumentType.XML,
                                    url=xml_url,
                                    filename=None,
                                )
                            )

                    if len(laws) < self.page_limit:
                        break
                    offset += self.page_limit

        return list(documents.values())

    async def get_document(self, document: Document, instance: DocumentInstance) -> bytes:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(instance.url)
            response.raise_for_status()
            return response.content

    def _api_key(self) -> str:
        load_dotenv(dotenv_path=".env")
        api_key = (
            os.getenv("CONGRESS_API_KEY")
            or os.getenv("CONGRESS_GOV_API_KEY")
            or os.getenv("API_CONGRESS_GOV_KEY")
        )
        if not api_key:
            raise RuntimeError(
                "Congress API key missing. Set CONGRESS_API_KEY or CONGRESS_GOV_API_KEY."
            )
        return api_key

    @staticmethod
    def _cutoff_date(value: datetime) -> date:
        if value.tzinfo is None:
            normalized = value
        else:
            normalized = value.astimezone(timezone.utc).replace(tzinfo=None)
        return normalized.date()

    @staticmethod
    def _congress_for_date(value: date) -> int:
        return ((value.year - 1789) // 2) + 1

    @staticmethod
    def _action_date(bill: dict[str, Any]) -> date | None:
        latest_action = bill.get("latestAction") or {}
        return CongressRetriever._safe_date(latest_action.get("actionDate"))

    @staticmethod
    def _safe_date(value: Any) -> date | None:
        if not value:
            return None
        as_text = str(value).strip()
        if "T" in as_text:
            as_text = as_text.split("T", 1)[0]
        try:
            return datetime.strptime(as_text, "%Y-%m-%d").date()
        except ValueError:
            return None

    async def _bill_xml_url(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        congress: str,
        bill_type: str,
        bill_number: str,
    ) -> str | None:
        payload = await self._fetch_json(
            client=client,
            path=f"/bill/{congress}/{bill_type}/{bill_number}/text",
            params={"api_key": api_key, "format": "json"},
        )
        versions = payload.get("textVersions", [])
        latest_xml_url: str | None = None
        latest_version_date: datetime | None = None

        for version in versions:
            version_date = self._safe_datetime(version.get("date"))
            formats = version.get("formats", [])
            for format_item in formats:
                fmt_type = str(format_item.get("type", "")).lower()
                url = str(format_item.get("url", "")).strip()
                if not url:
                    continue
                is_xml = "xml" in fmt_type or url.lower().endswith(".xml")
                if not is_xml:
                    continue
                if latest_version_date is None or (
                    version_date is not None and version_date > latest_version_date
                ):
                    latest_version_date = version_date
                    latest_xml_url = url

        return latest_xml_url

    @staticmethod
    def _safe_datetime(value: Any) -> datetime | None:
        if not value:
            return None
        as_text = str(value).strip()
        if not as_text:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(as_text, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None

    async def _fetch_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        response = await client.get(f"{self.api_base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

