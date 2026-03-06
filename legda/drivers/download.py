from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import selectinload

from legda.models import (
    Document,
    DocumentStatus,
)
from legda.plugins.base import RetrievalPlugin


class DownloadDriver:
    """Download pending documents and persist results via merge."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        plugins: dict[str, RetrievalPlugin],
    ) -> None:
        self.session_factory = session_factory
        self.plugins = plugins

    async def run(self) -> dict[str, int]:
        downloaded_counts = {plugin_key: 0 for plugin_key in self.plugins}
        documents = await self._load_detached_documents()

        for document in documents:
            plugin = self.plugins.get(document.plugin)
            if plugin is None:
                document.status = self._final_status(document)
                await self._merge_document(document)
                continue

            document.status = DocumentStatus.FETCHING
            await self._merge_document(document)

            try:
                await plugin.download_document(document)
            except Exception:
                pass

            document.status = self._final_status(document)
            await self._merge_document(document)

            if document.status == DocumentStatus.FETCHED:
                downloaded_counts[document.plugin] += 1

        return downloaded_counts

    async def _load_detached_documents(self) -> list[Document]:
        async with self.session_factory() as session:
            stmt = (
                select(Document)
                .where(Document.status == DocumentStatus.TO_FETCH)
                .options(selectinload(Document.instances))
                .order_by(Document.plugin, Document.publication_date, Document.document_id)
            )
            documents = list((await session.scalars(stmt)).all())
            session.expunge_all()
            return documents

    async def _merge_document(self, document: Document) -> None:
        async with self.session_factory() as session, session.begin():
            await session.merge(document)

    @staticmethod
    def _final_status(document: Document) -> DocumentStatus:
        if any(instance.filename for instance in document.instances):
            return DocumentStatus.FETCHED
        return DocumentStatus.FAILED
