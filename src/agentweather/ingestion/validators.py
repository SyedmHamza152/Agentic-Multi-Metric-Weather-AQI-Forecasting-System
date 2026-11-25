"""Data validation: make sure what we got from an API is complete and physically plausible."""
from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np
import pandas as pd

from agentweather.ingestion.schemas import DatasetSpec
from agentweather.utils.exceptions import DataValidationError
from agentweather.utils.logger import get_logger

logger = get_logger(__name__)


def safe_float(value: Any) -> Optional[float]:
    """Convert to float, or return None for anything unusable (None, '-', 'abc', NaN, inf)."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def validate_dataframe(df: pd.DataFrame, spec: DatasetSpec) -> pd.DataFrame:
    """Check and clean a DataFrame against its DatasetSpec.

    - Raises DataValidationError if there is no data or a critical column is missing.
    - Out-of-range values become NaN (with a warning), because a sensor glitch should not
      be stored as if it were real.
    - Rows still missing a critical value are dropped.
    - Time columns are converted to clean UTC text; columns come out in the spec's order.
    """
    if df is None or df.empty:
        raise DataValidationError(f"{spec.name}: no rows to validate")

    missing = [col for col in spec.critical if col not in df.columns]
    if missing:
        raise DataValidationError(f"{spec.name}: missing critical columns {missing}")

    df = df.copy()
    for col in spec.columns:
        if col not in df.columns:
            df[col] = np.nan

    for col, fmt in spec.time_columns.items():
        parsed = pd.to_datetime(df[col], utc=True, errors="coerce")
        df[col] = parsed.dt.strftime(fmt)

    for col, (low, high) in spec.ranges.items():
        df[col] = pd.to_numeric(df[col], errors="coerce")
        out_of_range = df[col].notna() & ((df[col] < low) | (df[col] > high))
        if out_of_range.any():
            logger.warning(
                "%s: %d value(s) in '%s' outside [%s, %s]; set to missing",
                spec.name, int(out_of_range.sum()), col, low, high,
            )
            df.loc[out_of_range, col] = np.nan

    before = len(df)
    df = df.dropna(subset=list(spec.critical))
    dropped = before - len(df)
    if dropped:
        logger.warning("%s: dropped %d row(s) missing critical values", spec.name, dropped)
    if df.empty:
        raise DataValidationError(f"{spec.name}: every row failed validation")

    return df[list(spec.columns)].reset_index(drop=True)
