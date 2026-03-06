from __future__ import annotations

from legda.plugins.federal_register import FederalRegisterRetriever


class OpmFederalRegisterRetriever(FederalRegisterRetriever):
    """Retrieve OPM Federal Register actions."""

    plugin_key = "opm_federal_register"
    document_type = "opm_federal_register"
    federal_register_conditions = {
        "conditions[agencies][]": "personnel-management-office",
    }

