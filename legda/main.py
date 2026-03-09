from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from legda.db import (
    create_session_factory,
    initialize_database,
)
from legda.drivers import (
    DiscoveryDriver,
    DownloadDriver,
)
from legda.plugins import build_plugins


DATA_DIR = Path("data")


async def run() -> None:
    load_dotenv(dotenv_path=".env")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    database_url = f"sqlite+aiosqlite:///{(DATA_DIR / 'legda.db').as_posix()}"
    engine, session_factory = create_session_factory(database_url)
    await initialize_database(engine)

    plugins = build_plugins(data_directory=DATA_DIR)
    discovery_driver = DiscoveryDriver(session_factory=session_factory, plugins=plugins)
    download_driver = DownloadDriver(session_factory=session_factory, plugins=plugins)

    discovery_counts = await discovery_driver.run()
    download_counts = await download_driver.run()

    await engine.dispose()

    print("Discovery complete:")
    for plugin_key, count in discovery_counts.items():
        print(f"- {plugin_key}: upserted {count} documents")

    print("Download complete:")
    for plugin_key, count in download_counts.items():
        print(f"- {plugin_key}: fetched {count} documents")
