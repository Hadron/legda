from __future__ import annotations

from datetime import (
    date,
    datetime,
    timezone,
)
from pathlib import PurePosixPath
from urllib.parse import unquote
from urllib.parse import urljoin
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from legda.models import (
    Document,
    DocumentInstance,
    DocumentStatus,
    DocumentType,
)
from legda.plugins.base import RetrievalPlugin


class OpmMemoRetriever(RetrievalPlugin):
    """Retrieve CHCOC published memos from OPM."""

    plugin_key = "opm_memos"
    document_type = "opm_memos"
    preferred_document_types = (DocumentType.PDF, DocumentType.HTML)
    index_url = "https://www.opm.gov/chcoc/published-memos/"
    timeout_seconds = 30.0
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async def get_documents(self, since: datetime) -> list[Document]:
        cutoff_date = self._cutoff_date(since)
        html = await self._fetch_text(self.index_url)
        soup = BeautifulSoup(html, "lxml")
        table = soup.select_one("table#transmittalsTable") or soup.find("table")
        if table is None:
            return []

        headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
        rows = table.find_all("tr")
        documents: dict[tuple[date, str], Document] = {}

        for row in rows:
            cells = row.find_all(["th", "td"], recursive=False)
            if not cells:
                continue

            publication_date = self._extract_date(cells, headers)
            if publication_date is None or publication_date < cutoff_date:
                continue

            title_link = self._extract_title_link(cells, headers)
            if title_link is None:
                continue

            title = title_link.get_text(" ", strip=True)
            source_url = self._absolute_url(title_link["href"])
            reference_id = self._extract_reference_id(cells, headers)
            document_id = reference_id or self._slug_from_url(source_url)
            key = (publication_date, document_id)

            document = documents.get(key)
            if document is None:
                document = Document(
                    publication_date=publication_date,
                    document_id=document_id,
                    plugin=self.plugin_key,
                    title=title,
                    status=DocumentStatus.TO_FETCH,
                )
                documents[key] = document

            self._add_instances(document, row, source_url)

        return list(documents.values())

    async def get_document(self, document: Document, instance: DocumentInstance) -> bytes:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(instance.url)
            response.raise_for_status()
            return response.content

    def _add_instances(self, document: Document, row, primary_url: str) -> None:
        existing = {(instance.document_type, instance.url) for instance in document.instances}

        primary_type = self._document_type_for_url(primary_url)
        primary_key = (primary_type, primary_url)
        if primary_key not in existing:
            document.instances.append(
                DocumentInstance(
                    publication_date=document.publication_date,
                    document_id=document.document_id,
                    document_type=primary_type,
                    url=primary_url,
                    filename=None,
                )
            )
            existing.add(primary_key)

        for link in row.find_all("a", href=True):
            url = self._absolute_url(link["href"])
            doc_type = self._document_type_for_url(url)
            if doc_type != DocumentType.PDF:
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

    def _extract_date(self, cells, headers: list[str]) -> date | None:
        date_index = self._column_index(headers, {"date"})
        texts = [cell.get_text(" ", strip=True) for cell in cells]

        candidates: list[str] = []
        if date_index is not None and date_index < len(texts):
            candidates.append(texts[date_index])
        candidates.extend(texts)

        for value in candidates:
            parsed = self._parse_date(value)
            if parsed is not None:
                return parsed
        return None

    def _extract_reference_id(self, cells, headers: list[str]) -> str | None:
        ref_index = self._column_index(headers, {"reference id", "reference"})
        if ref_index is None or ref_index >= len(cells):
            return None
        value = " ".join(cells[ref_index].get_text(" ", strip=True).split())
        return value or None

    def _extract_title_link(self, cells, headers: list[str]):
        title_index = self._column_index(
            headers, {"title", "memo", "memorandum", "subject"}
        )
        if title_index is not None and title_index < len(cells):
            link = cells[title_index].find("a", href=True)
            if link is not None:
                return link

        for cell in cells:
            link = cell.find("a", href=True)
            if link is None:
                continue
            href = self._absolute_url(link["href"])
            if self._document_type_for_url(href) == DocumentType.HTML:
                return link
        return None

    @staticmethod
    def _column_index(headers: list[str], accepted: set[str]) -> int | None:
        for index, header in enumerate(headers):
            normalized = " ".join(header.split())
            if any(candidate in normalized for candidate in accepted):
                return index
        return None

    @staticmethod
    def _parse_date(value: str) -> date | None:
        cleaned = " ".join(value.replace(",", "").split())
        for fmt in (
            "%m/%d/%Y",
            "%A %B %d %Y",
            "%a %B %d %Y",
            "%B %d %Y",
            "%b %d %Y",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(cleaned, fmt).date()
            except ValueError:
                continue
        return None

    def _absolute_url(self, href: str) -> str:
        return urljoin(self.index_url, href)

    @staticmethod
    def _slug_from_url(url: str) -> str:
        path = PurePosixPath(urlparse(url).path.rstrip("/"))
        slug = path.stem if path.suffix else path.name
        slug = unquote(slug)
        return slug or "opm-memo"

    @staticmethod
    def _document_type_for_url(url: str) -> DocumentType:
        if PurePosixPath(urlparse(url).path).suffix.lower() == ".pdf":
            return DocumentType.PDF
        return DocumentType.HTML

    @staticmethod
    def _cutoff_date(since: datetime) -> date:
        if since.tzinfo is None:
            normalized = since
        else:
            normalized = since.astimezone(timezone.utc).replace(tzinfo=None)
        return normalized.date()

    async def _fetch_text(self, url: str) -> str:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            headers=self.request_headers,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
