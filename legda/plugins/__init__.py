from pathlib import Path

from .base import RetrievalPlugin
from .executive_orders import ExecutiveOrderRetriever


PLUGIN_CLASSES: dict[str, type[RetrievalPlugin]] = {
    ExecutiveOrderRetriever.plugin_key: ExecutiveOrderRetriever,
}


def build_plugins(data_directory: Path | str = "data") -> dict[str, RetrievalPlugin]:
    return {
        plugin_key: plugin_class(data_directory=data_directory)
        for plugin_key, plugin_class in PLUGIN_CLASSES.items()
    }


__all__ = [
    "ExecutiveOrderRetriever",
    "PLUGIN_CLASSES",
    "RetrievalPlugin",
    "build_plugins",
]
