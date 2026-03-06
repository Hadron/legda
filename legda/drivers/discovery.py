from __future__ import annotations

from datetime import datetime

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import selectinload

from legda.models import (
    Document,
    DocumentInstance,
    DocumentStatus,
)
from legda.plugins.base import RetrievalPlugin


class DiscoveryDriver:
    """Run plugin discovery and upsert documents with minimal field coupling."""

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
            discovered = await plugin.get_documents(since)
            async with self.session_factory() as session, session.begin():
                for document in discovered:
                    await self._upsert_document(session, plugin_key, document)
            counts[plugin_key] = len(discovered)

        return counts

    async def _upsert_document(
        self,
        session: AsyncSession,
        plugin_key: str,
        incoming: Document,
    ) -> None:
        incoming.plugin = plugin_key
        existing = await session.get(
            Document,
            (incoming.publication_date, incoming.document_id),
            options=(selectinload(Document.instances),),
        )

        if existing is None:
            incoming.status = DocumentStatus.TO_FETCH
            await session.merge(incoming)
            return

        incoming.status = (
            DocumentStatus.FETCHED
            if existing.status == DocumentStatus.FETCHED
            else DocumentStatus.TO_FETCH
        )

        existing_instances = {
            self._instance_key(instance): instance for instance in existing.instances
        }
        incoming_instances = {
            self._instance_key(instance): instance for instance in incoming.instances
        }

        merged_instances: list[DocumentInstance] = []
        for key in existing_instances.keys() | incoming_instances.keys():
            if key in incoming_instances:
                candidate = incoming_instances[key]
                if candidate.filename is None:
                    candidate.filename = existing_instances.get(key, candidate).filename
                merged_instances.append(candidate)
                continue
            merged_instances.append(existing_instances[key])

        incoming.instances = merged_instances
        await session.merge(incoming)

    @staticmethod
    def _instance_key(instance: DocumentInstance) -> tuple[object, ...]:
        return tuple(
            getattr(instance, column.key)
            for column in inspect(DocumentInstance).primary_key
        )
