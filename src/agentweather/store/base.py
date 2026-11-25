"""The storage contract. CSV implements it now; a Hopsworks version will implement it later,
so the pipelines and agents do not need to change when we switch."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

import pandas as pd


class DataStore(ABC):
    @abstractmethod
    def save(self, dataset: str, df: pd.DataFrame, key_columns: Sequence[str]) -> int:
        """Add rows to a dataset. Rows with the same key_columns replace the old ones.
        Returns the number of NEW rows added."""

    @abstractmethod
    def load(self, dataset: str) -> pd.DataFrame:
        """Return the whole dataset (an empty DataFrame if it does not exist yet)."""

    @abstractmethod
    def exists(self, dataset: str) -> bool:
        """True if the dataset has been saved before."""
