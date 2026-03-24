from __future__ import annotations

import re
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
from bs4 import Tag

from legda.models import (
    Document,
    DocumentInstance,
    DocumentStatus,
    DocumentType,
)
from legda.plugins.base import RetrievalPlugin


class OmbMemoRetriever(RetrievalPlugin):
    """Retrieve OMB memoranda from the White House OMB guidance index."""

    plugin_key = "omb_memos"
    document_type = "omb_memos"
    preferred_document_types = (DocumentType.PDF, DocumentType.HTML)
    index_url = "https://www.whitehouse.gov/omb/information-resources/guidance/memoranda/"
    timeout_seconds = 30.0
    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    memo_id_pattern = re.compile(r"\b(M-\d{2}-\d{2})\b", re.IGNORECASE)

    async def get_documents(self, since: datetime) -> list[Document]:
        cutoff_date = self._cutoff_date(since)
        html = await self._fetch_text(self.index_url)
        soup = BeautifulSoup(html, "lxml")
        documents: dict[tuple[date, str], Document] = {}

        for heading in soup.find_all(["h2", "h3"]):
            heading_text = heading.get_text(" ", strip=True)
            if not heading_text.lower().startswith("memoranda "):
                continue

            for memo_item in self._memo_items_for_heading(heading):
                link = memo_item.find("a", href=True)
                if link is None:
                    continue

                publication_date = self._extract_date(memo_item.get_text(" ", strip=True))
                if publication_date is None or publication_date < cutoff_date:
                    continue

                title = " ".join(link.get_text(" ", strip=True).split())
                url = self._absolute_url(link["href"])
                document_id = self._extract_document_id(title, url)
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

                self._add_instance(document, url)

        return list(documents.values())

    async def get_document(self, document: Document, instance: DocumentInstance) -> bytes:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            headers=self.request_headers,
            follow_redirects=True,
        ) as client:
            response = await client.get(instance.url)
            response.raise_for_status()
            return response.content

    def _memo_items_for_heading(self, heading: Tag) -> list[Tag]:
        memo_items: list[Tag] = []
        sibling = heading.find_next_sibling()
        while sibling is not None:
            if sibling.name in {"h2", "h3"}:
                break
            if sibling.name == "ul":
                memo_items.extend(sibling.find_all("li", recursive=False))
            sibling = sibling.find_next_sibling()
        return memo_items

    def _add_instance(self, document: Document, url: str) -> None:
        document_type = self._document_type_for_url(url)
        if any(
            instance.document_type == document_type and instance.url == url
            for instance in document.instances
        ):
            return
        document.instances.append(
            DocumentInstance(
                publication_date=document.publication_date,
                document_id=document.document_id,
                document_type=document_type,
                url=url,
                filename=None,
            )
        )

    def _extract_document_id(self, title: str, url: str) -> str:
        match = self.memo_id_pattern.search(title)
        if match:
            return match.group(1).upper()
        return self._slug_from_url(url)

    @staticmethod
    def _extract_date(text: str) -> date | None:
        match = re.search(r"\(([^()]*)\)\s*$", text)
        if match is None:
            return None
        cleaned = " ".join(match.group(1).replace(",", "").split())
        for fmt in ("%B %d %Y", "%b %d %Y", "%Y-%m-%d"):
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
        return slug or "omb-memo"

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

