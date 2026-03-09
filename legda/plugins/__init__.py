from pathlib import Path

from .base import RetrievalPlugin
from .congress import CongressRetriever
from .executive_orders import ExecutiveOrderRetriever
from .federal_register import FederalRegisterRetriever
from .opm_federal_register import OpmFederalRegisterRetriever
from .opm_memos import OpmMemoRetriever


PLUGIN_CLASSES: dict[str, type[RetrievalPlugin]] = {
    CongressRetriever.plugin_key: CongressRetriever,
    ExecutiveOrderRetriever.plugin_key: ExecutiveOrderRetriever,
    OpmFederalRegisterRetriever.plugin_key: OpmFederalRegisterRetriever,
    OpmMemoRetriever.plugin_key: OpmMemoRetriever,
}


def build_plugins(data_directory: Path | str = "data") -> dict[str, RetrievalPlugin]:
    return {
        plugin_key: plugin_class(data_directory=data_directory)
        for plugin_key, plugin_class in PLUGIN_CLASSES.items()
    }


__all__ = [
    "CongressRetriever",
    "ExecutiveOrderRetriever",
    "FederalRegisterRetriever",
    "OpmFederalRegisterRetriever",
    "OpmMemoRetriever",
    "PLUGIN_CLASSES",
    "RetrievalPlugin",
    "build_plugins",
]
