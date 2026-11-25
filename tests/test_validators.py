import numpy as np
import pandas as pd
import pytest

from agentweather.ingestion.schemas import OPENWEATHER_WEATHER
from agentweather.ingestion.validators import safe_float, validate_dataframe
from agentweather.utils.exceptions import DataValidationError


def _row(**overrides):
    row = {
        "timestamp_utc": "2024-11-14T04:00:00Z", "city": "Lahore",
        "temperature_c": 20.0, "humidity_pct": 50, "wind_speed_ms": 2.0,
    }
    row.update(overrides)
    return row


def test_safe_float_handles_bad_values():
    assert safe_float("12.5") == 12.5
    assert safe_float("-") is None
    assert safe_float(None) is None
    assert safe_float(float("nan")) is None


def test_valid_row_passes_and_columns_follow_spec():
    out = validate_dataframe(pd.DataFrame([_row()]), OPENWEATHER_WEATHER)
    assert list(out.columns) == list(OPENWEATHER_WEATHER.columns)
    assert len(out) == 1


def test_out_of_range_non_critical_value_becomes_nan():
    out = validate_dataframe(pd.DataFrame([_row(uv_index=99)]), OPENWEATHER_WEATHER)
    assert np.isnan(out.loc[0, "uv_index"])


def test_out_of_range_critical_value_drops_row():
    df = pd.DataFrame([_row(), _row(timestamp_utc="2024-11-14T05:00:00Z", humidity_pct=500)])
    out = validate_dataframe(df, OPENWEATHER_WEATHER)
    assert len(out) == 1


def test_empty_dataframe_raises():
    with pytest.raises(DataValidationError):
        validate_dataframe(pd.DataFrame(), OPENWEATHER_WEATHER)


def test_missing_critical_column_raises():
    with pytest.raises(DataValidationError):
        validate_dataframe(pd.DataFrame([{"timestamp_utc": "2024-11-14T04:00:00Z"}]), OPENWEATHER_WEATHER)


def test_all_rows_invalid_raises():
    with pytest.raises(DataValidationError):
        validate_dataframe(pd.DataFrame([_row(temperature_c=900)]), OPENWEATHER_WEATHER)
