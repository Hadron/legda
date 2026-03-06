from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from legda.models import Document
from legda.models import DocumentInstance
from legda.models import DocumentStatus
from legda.plugins.base import RetrievalPlugin


class DiscoveryDriver:
    """Discover documents through plugins and upsert them into persistent storage."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        plugins: dict[str, RetrievalPlugin],
    ) -> None:
        self.session_factory = session_factory
        self.plugins = plugins

    async def run(self, since: datetime) -> dict[str, int]:
        counts: dict[str, int] = {}
        for plugin_key, plugin in self.plugins.items():
            discovered_documents = await plugin.get_documents(since)
            async with self.session_factory() as session:
                async with session.begin():
                    counts[plugin_key] = await self._upsert_documents(
                        session=session,
                        plugin_key=plugin_key,
                        documents=discovered_documents,
                    )
        return counts

    async def _upsert_documents(
        self,
        session: AsyncSession,
        plugin_key: str,
        documents: list[Document],
    ) -> int:
        upserted_count = 0
        for incoming in documents:
            stmt = (
                select(Document)
                .where(
                    Document.publication_date == incoming.publication_date,
                    Document.document_id == incoming.document_id,
                )
                .options(selectinload(Document.instances))
            )
            existing = (await session.scalars(stmt)).first()
            if existing is None:
                session.add(self._new_document_from(incoming, plugin_key))
                upserted_count += 1
                continue

            self._update_document(existing, incoming, plugin_key)
            upserted_count += 1

        return upserted_count

    def _new_document_from(self, incoming: Document, plugin_key: str) -> Document:
        persisted = Document(
            publication_date=incoming.publication_date,
            document_id=incoming.document_id,
            plugin=plugin_key,
            title=incoming.title,
            status=DocumentStatus.TO_FETCH,
        )
        for instance in incoming.instances:
            persisted.instances.append(
                DocumentInstance(
                    publication_date=incoming.publication_date,
                    document_id=incoming.document_id,
                    document_type=instance.document_type,
                    url=instance.url,
                    filename=instance.filename,
                )
            )
        return persisted

    def _update_document(
        self, existing: Document, incoming: Document, plugin_key: str
    ) -> None:
        existing.title = incoming.title
        existing.plugin = plugin_key
        if existing.status != DocumentStatus.FETCHED:
            existing.status = DocumentStatus.TO_FETCH

        existing_instances = {
            (instance.document_type, instance.url) for instance in existing.instances
        }
        for instance in incoming.instances:
            key = (instance.document_type, instance.url)
            if key in existing_instances:
                continue
            existing.instances.append(
                DocumentInstance(
                    publication_date=existing.publication_date,
                    document_id=existing.document_id,
                    document_type=instance.document_type,
                    url=instance.url,
                    filename=instance.filename,
                )
            )
            existing_instances.add(key)

