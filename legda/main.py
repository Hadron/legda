from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from legda.models import Document
from legda.plugins.executive_orders import ExecutiveOrderRetriever


OUTPUT_DIR = Path("data/executive_orders")
SINCE = datetime(2026, 2, 19, tzinfo=timezone.utc)


def _filename_for(document: Document) -> str:
    extension = "pdf" if document.pdf_url else "html"
    return f"{document.publication_date.isoformat()}_{document.document_number}.{extension}"


async def _download_document(
    retriever: ExecutiveOrderRetriever, document: Document, semaphore: asyncio.Semaphore
) -> Path:
    async with semaphore:
        payload = await retriever.get_document(document)
        output_path = OUTPUT_DIR / _filename_for(document)
        output_path.write_bytes(payload)
        return output_path


async def run() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    retriever = ExecutiveOrderRetriever()

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
        f"{SINCE.date().isoformat()} to {OUTPUT_DIR.resolve()}"
    )
    for path in downloaded_paths:
        print(f"- {path}")


if __name__ == "__main__":
    asyncio.run(run())

