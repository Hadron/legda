from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from legda.models import Document
from legda.plugins.executive_orders import ExecutiveOrderRetriever


DATA_DIR = Path("data")
SINCE = datetime(2026, 2, 19, tzinfo=timezone.utc)


async def _download_document(
    retriever: ExecutiveOrderRetriever, document: Document, semaphore: asyncio.Semaphore
) -> Path:
    async with semaphore:
        return await retriever.download_document(document)


async def run() -> None:
    retriever = ExecutiveOrderRetriever(data_directory=DATA_DIR)

    documents = await retriever.documents_since(SINCE)
    if not documents:
        print(f"No executive orders found since {SINCE.date().isoformat()}.")
        return

    ordered_documents = sorted(
        documents, key=lambda doc: (doc.publication_date, doc.document_number)
    )

    semaphore = asyncio.Semaphore(5)
    downloaded_paths = await asyncio.gather(
        *(_download_document(retriever, document, semaphore) for document in ordered_documents)
    )

    print(
        f"Downloaded {len(downloaded_paths)} executive orders published since "
        f"{SINCE.date().isoformat()} to "
        f"{(retriever.data_directory / retriever.document_type).resolve()}"
    )
    for path in downloaded_paths:
        print(f"- {path}")


if __name__ == "__main__":
    asyncio.run(run())
