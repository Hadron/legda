from __future__ import annotations

from legda.plugins.federal_register import FederalRegisterRetriever


class ExecutiveOrderRetriever(FederalRegisterRetriever):
    """Retrieve executive orders from the Federal Register API."""

    plugin_key = "executive_orders"
    document_type = "executive_orders"
    federal_register_conditions = {
        "conditions[presidential_document_type][]": "executive_order",
    }

