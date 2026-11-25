"""CSV storage for early testing: one file per dataset, e.g. data/raw/aqicn_air.csv."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence, Union

import pandas as pd

from agentweather.store.base import DataStore
from agentweather.utils.exceptions import AgentWeatherError
from agentweather.utils.logger import get_logger

logger = get_logger(__name__)


class CSVStore(DataStore):
    def __init__(self, base_dir: Union[str, Path]) -> None:
        self.base_dir = Path(base_dir)

    def _path(self, dataset: str) -> Path:
        return self.base_dir / f"{dataset}.csv"

    def exists(self, dataset: str) -> bool:
        return self._path(dataset).exists()

    def load(self, dataset: str) -> pd.DataFrame:
        path = self._path(dataset)
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path)

    def save(self, dataset: str, df: pd.DataFrame, key_columns: Sequence[str]) -> int:
        if df is None or df.empty:
            logger.info("%s: nothing to save", dataset)
            return 0

        missing = [c for c in key_columns if c not in df.columns]
        if missing:
            raise AgentWeatherError(f"{dataset}: key columns missing from data: {missing}")

        path = self._path(dataset)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            existing = pd.read_csv(path)
            combined = pd.concat([existing, df], ignore_index=True)
        else:
            existing = pd.DataFrame()
            combined = df.copy()

        # Compare keys as text so "1234" from the CSV equals 1234 from the API.
        for col in key_columns:
            combined[col] = combined[col].astype(str)
        combined = combined.drop_duplicates(subset=list(key_columns), keep="last")
        combined = combined.sort_values(list(key_columns)).reset_index(drop=True)

        # Write to a temporary file first, then swap it in, so a crash never leaves a half-written CSV.
        tmp_path = path.with_suffix(".csv.tmp")
        combined.to_csv(tmp_path, index=False)
        os.replace(tmp_path, path)

        added = len(combined) - len(existing)
        logger.info("%s: saved to %s (%d new row(s), %d total)", dataset, path.name, added, len(combined))
        return added
