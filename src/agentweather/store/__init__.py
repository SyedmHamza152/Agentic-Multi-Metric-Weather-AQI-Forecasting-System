"""Storage layer. The rest of the code only talks to the DataStore interface."""
from agentweather.config.settings import Settings
from agentweather.store.base import DataStore
from agentweather.store.csv_store import CSVStore
from agentweather.utils.exceptions import ConfigError


def get_store(settings: Settings) -> DataStore:
    """Return the storage backend chosen in settings.yaml (storage.backend)."""
    backend = settings.storage.backend
    if backend == "csv":
        return CSVStore(settings.storage.raw_dir)
    if backend == "hopsworks":
        raise NotImplementedError(
            "The Hopsworks store is added in a later phase. Use backend: csv for now."
        )
    raise ConfigError(f"Unknown storage backend '{backend}' (use 'csv' or 'hopsworks')")


__all__ = ["DataStore", "CSVStore", "get_store"]
