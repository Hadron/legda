# legda

`legda` is a small asyncio-based data retrieval app for government documents.

## Architecture

- `Document` (`legda/models.py`) is the hashable metadata model shared across retrievers.
- `RetrievalPlugin` (`legda/plugins/base.py`) defines the plugin contract:
  - `documents_since(datetime) -> set[Document]`
  - `get_document(Document) -> bytes`
  - `download_document(Document) -> Path`
- `ExecutiveOrderRetriever` (`legda/plugins/executive_orders.py`) implements this contract using the Federal Register API.

## Run

```bash
python3 -m pip install -r requirements.txt
python3 -m legda
```

This fetches and downloads all executive orders published on or after `2026-02-19` into:

- `data/<document_type>/<document_date>-<document_number>.<extension>`
