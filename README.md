# legda

`legda` is a small asyncio-based data retrieval app for government documents.

## Architecture

- `Document` and `DocumentInstance` (`legda/models.py`) are SQLAlchemy models for persistent state.
- `RetrievalPlugin` (`legda/plugins/base.py`) defines the plugin contract:
  - `get_documents(datetime) -> list[Document]`
  - `get_document(Document, DocumentInstance) -> bytes`
  - `download_document(Document) -> list[DocumentInstance]`
- `FederalRegisterRetriever` (`legda/plugins/federal_register.py`) provides shared Federal Register retrieval logic.
- `ExecutiveOrderRetriever` (`legda/plugins/executive_orders.py`) uses that base for executive orders.
- `OpmFederalRegisterRetriever` (`legda/plugins/opm_federal_register.py`) uses that base for OPM actions.
- `DiscoveryDriver` (`legda/drivers/discovery.py`) runs all plugins and upserts discovered documents.
- `DownloadDriver` (`legda/drivers/download.py`) fetches documents with `status=to_fetch`.
- `FetchDate` (`legda/models.py`) stores per-plugin successful discovery timestamps.
- Plugin registration lives in `legda/plugins/__init__.py` via a plugin-key to class map.

## Run

```bash
python3 -m pip install -r requirements.txt
python3 -m legda
```

This fetches and downloads executive orders into:

- `data/<document_type>/<publication_date>-<document_id>.<extension>`

Discovery uses the last successful plugin fetch timestamp from `fetch_dates`; new
plugins start from `DEFAULT_DISCOVERY_SINCE` in `legda/constants.py`.
