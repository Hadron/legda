from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from legda.models import Document
from legda.models import DocumentInstance
from legda.models import DocumentStatus
from legda.plugins.base import RetrievalPlugin


class DownloadDriver:
    """Download pending documents and persist file locations."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        plugins: dict[str, RetrievalPlugin],
    ) -> None:
        self.session_factory = session_factory
        self.plugins = plugins

    async def run(self) -> dict[str, int]:
        downloaded_counts = {plugin_key: 0 for plugin_key in self.plugins}

        async with self.session_factory() as session:
            documents = await self._load_documents_to_fetch(session)

        for persisted_document in documents:
            plugin = self.plugins.get(persisted_document.plugin)
            if plugin is None:
                async with self.session_factory() as session:
                    async with session.begin():
                        await self._persist_download_result(
                            session=session,
                            transient_document=self._to_transient_document(persisted_document),
                        )
                continue

            async with self.session_factory() as session:
                async with session.begin():
                    await self._set_document_status(
                        session=session,
                        publication_date=persisted_document.publication_date,
                        document_id=persisted_document.document_id,
                        status=DocumentStatus.FETCHING,
                    )

            transient_document = self._to_transient_document(persisted_document)
            success = False
            try:
                downloaded_instances = await plugin.download_document(transient_document)
                success = any(instance.filename for instance in downloaded_instances)
            except Exception:
                success = False

            async with self.session_factory() as session:
                async with session.begin():
                    await self._persist_download_result(
                        session=session,
                        transient_document=transient_document,
                    )

            if success:
                downloaded_counts[persisted_document.plugin] += 1

        return downloaded_counts

    async def _load_documents_to_fetch(self, session: AsyncSession) -> list[Document]:
        stmt = (
            select(Document)
            .where(
                Document.status == DocumentStatus.TO_FETCH,
            )
            .options(selectinload(Document.instances))
            .order_by(Document.plugin, Document.publication_date, Document.document_id)
        )
        return list((await session.scalars(stmt)).all())

    def _to_transient_document(self, persisted_document: Document) -> Document:
        transient = Document(
            publication_date=persisted_document.publication_date,
            document_id=persisted_document.document_id,
            plugin=persisted_document.plugin,
            title=persisted_document.title,
            status=persisted_document.status,
        )
        for instance in persisted_document.instances:
            transient.instances.append(
                DocumentInstance(
                    id=instance.id,
                    publication_date=instance.publication_date,
                    document_id=instance.document_id,
                    document_type=instance.document_type,
                    url=instance.url,
                    filename=instance.filename,
                )
            )
        return transient

    async def _persist_download_result(
        self,
        session: AsyncSession,
        transient_document: Document,
    ) -> None:
        stmt = (
            select(Document)
            .where(
                Document.publication_date == transient_document.publication_date,
                Document.document_id == transient_document.document_id,
            )
            .options(selectinload(Document.instances))
        )
        persisted_document = (await session.scalars(stmt)).one()

        instance_by_id = {instance.id: instance for instance in persisted_document.instances}
        for instance in transient_document.instances:
            if instance.id is None:
                continue
            persisted_instance = instance_by_id.get(instance.id)
            if persisted_instance is None:
                continue
            persisted_instance.filename = instance.filename

        if any(instance.filename for instance in persisted_document.instances):
            persisted_document.status = DocumentStatus.FETCHED
        else:
            persisted_document.status = DocumentStatus.FAILED

    async def _set_document_status(
        self,
        session: AsyncSession,
        publication_date: date,
        document_id: str,
        status: DocumentStatus,
    ) -> None:
        persisted_document = await session.get(
            Document, (publication_date, document_id)
        )
        if persisted_document is None:
            return
        persisted_document.status = status
