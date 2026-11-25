"""Tests for the OpenAQ v3 client: parsing and history assembly (fake HTTP)."""
import pandas as pd
import pytest

from agentweather.config.settings import OpenAQConfig
from agentweather.ingestion.openaq_client import (
    OpenAQClient,
    hourly_ugm3_frame,
    parse_locations,
    parse_measurements,
    sensors_by_pollutant,
)
from agentweather.utils.exceptions import ConfigError, DataValidationError


@pytest.fixture
def oa_config():
    return OpenAQConfig("https://api.openaq.org/v3", 30, 3, 25000, 10000, 31, 3)


@pytest.fixture
def locations_payload():
    return {
        "meta": {"found": 2},
        "results": [
            {"id": 11, "name": "Lahore Station",
             "coordinates": {"latitude": 31.55, "longitude": 74.34},
             "datetimeFirst": {"utc": "2019-01-01T00:00:00Z"},
             "datetimeLast": {"utc": "2025-01-01T00:00:00Z"},
             "sensors": [
                 {"id": 101, "parameter": {"name": "pm25", "units": "µg/m³"}},
                 {"id": 102, "parameter": {"name": "pm10", "units": "µg/m³"}},
                 {"id": 103, "parameter": {"name": "temperature", "units": "°C"}},
             ]},
            {"id": 22, "name": "PM-only Station",
             "coordinates": {"latitude": 32.0, "longitude": 75.0},
             "datetimeFirst": {"utc": "2025-01-01T00:00:00Z"},
             "datetimeLast": {"utc": "2026-01-01T00:00:00Z"},
             "sensors": [
                 {"id": 201, "parameter": {"name": "pm25", "units": "µg/m³"}},
             ]},
        ],
    }


@pytest.fixture
def measurements_payload():
    return {
        "meta": {"page": 1, "limit": 3, "found": ">3"},
        "results": [
            {"value": 55.0,
             "period": {"datetimeFrom": {"utc": "2024-01-01T00:00:00Z"}}},
            {"value": 65.0,
             "period": {"datetimeFrom": {"utc": "2024-01-01T01:00:00Z"}}},
        ],
    }


def test_parse_locations_sorted_by_pollutants_then_coverage(city, locations_payload):
    locs = parse_locations(locations_payload, city)
    assert [l["location_id"] for l in locs] == ["11", "22"]  # 2 pollutants beats 1
    assert locs[0]["pollutants"] == {"pm25", "pm10"}
    assert locs[0]["coverage_days"] > 2000


def test_parse_locations_requires_aqi_pollutants(city):
    payload = {"results": [{"id": 1, "name": "X", "sensors": [
        {"id": 9, "parameter": {"name": "temperature", "units": "°C"}}]}]}
    with pytest.raises(DataValidationError):
        parse_locations(payload, city)


def test_sensors_by_pollutant_groups_and_filters(locations_payload):
    grouped = sensors_by_pollutant(locations_payload["results"][0])
    assert set(grouped) == {"pm25", "pm10"}
    assert grouped["pm25"][0]["id"] == "101"


def test_parse_measurements_uses_period_start(measurements_payload):
    rows = parse_measurements(measurements_payload)
    assert rows[0] == {"timestamp_utc": "2024-01-01T00:00:00Z", "value": 55.0}


def test_missing_key_is_config_error(city, oa_config):
    with pytest.raises(ConfigError):
        OpenAQClient("", city, oa_config)


def test_fetch_history_end_to_end(city, oa_config, fakes):
    meas = {
        "meta": {"page": 1, "limit": 10000, "found": ">2"},
        "results": [
            {"value": 60.0,
             "period": {"datetimeFrom": {"utc": "2024-01-01T00:00:00Z"}}},
            {"value": 60.0,
             "period": {"datetimeFrom": {"utc": "2024-01-01T01:00:00Z"}}},
        ],
    }
    # locations + one measurements call per pollutant sensor (pm25, pm10)
    session = fakes.Session([
        fakes.Response(200, {"results": [{
            "id": 11, "name": "Lahore",
            "coordinates": {"latitude": 31.5, "longitude": 74.3},
            "sensors": [{"id": 101, "parameter": {"name": "pm25", "units": "µg/m³"}}],
        }]}),
        fakes.Response(200, meas),
    ])
    client = OpenAQClient("KEY", city, oa_config, session)
    df = client.fetch_history(
        pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
        pd.Timestamp("2024-01-03", tz="UTC").to_pydatetime(),
    )
    assert len(df) == 2
    assert df["location_id"].iloc[0] == "11"
    assert df["pm25"].tolist() == [60.0, 60.0]
    # 24h averaging window cannot be satisfied in 2 hours -> AQI stays missing
    assert df["us_aqi"].isna().all()
    # measurements endpoint is per-sensor
    assert "/sensors/101/measurements" in session.calls[1]["url"]


def test_fetch_history_validates_station(city, oa_config, fakes):
    session = fakes.Session([fakes.Response(200, {"results": []})])
    client = OpenAQClient("KEY", city, oa_config, session)
    with pytest.raises(DataValidationError):
        client.fetch_history(
            pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
            pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime(),
        )


def test_hourly_frame_averages_within_the_hour(city):
    pm25 = pd.DataFrame({
        "timestamp_utc": ["2024-01-01T00:10:00Z", "2024-01-01T00:50:00Z",
                          "2024-01-01T01:10:00Z"],
        "value": [10.0, 20.0, 30.0],
    })
    wide = hourly_ugm3_frame({"pm25": pm25},
                             {"location_id": "1", "location_name": "S"}, city)
    assert wide["pm25"].tolist() == [15.0, 30.0]  # hourly means
