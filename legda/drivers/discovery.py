from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import selectinload

from legda.constants import DEFAULT_DISCOVERY_SINCE
from legda.models import (
    Document,
    DocumentInstance,
    DocumentStatus,
    FetchDate,
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

    async def run(self) -> dict[str, int]:
        counts: dict[str, int] = {}

        for plugin_key, plugin in self.plugins.items():
            since = await self._since_for_plugin(plugin_key)
            try:
                discovered = await plugin.get_documents(since)
            except Exception:
                counts[plugin_key] = 0
                continue

            async with self.session_factory() as session, session.begin():
                for document in discovered:
                    await self._upsert_document(session, plugin_key, document)
                await session.merge(
                    FetchDate(
                        plugin=plugin_key,
                        date_fetched=datetime.now(timezone.utc),
                    )
                )
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

    async def _since_for_plugin(self, plugin_key: str) -> datetime:
        async with self.session_factory() as session:
            fetch_date = await session.get(FetchDate, plugin_key)

        if fetch_date is None:
            return DEFAULT_DISCOVERY_SINCE

        if fetch_date.date_fetched.tzinfo is None:
            return fetch_date.date_fetched.replace(tzinfo=timezone.utc)
        return fetch_date.date_fetched
