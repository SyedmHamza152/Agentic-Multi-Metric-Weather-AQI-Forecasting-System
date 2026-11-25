import dataclasses

import pandas as pd
import pytest

from agentweather.ingestion.aqicn_client import AQICNClient
from agentweather.utils.exceptions import (
    APIError,
    AuthenticationError,
    ConfigError,
    DataValidationError,
    RateLimitError,
)


def test_missing_token_is_a_config_error(city, aq_config):
    with pytest.raises(ConfigError):
        AQICNClient("", city, aq_config)


def test_fetch_feed_returns_current_and_forecast(city, aq_config, fakes, aqicn_payload):
    session = fakes.Session([fakes.Response(200, aqicn_payload)])
    current, forecast = AQICNClient("TOKEN", city, aq_config, session).fetch_feed()

    assert current.loc[0, "aqi"] == 187
    assert current.loc[0, "station_id"] == "1234"
    assert current.loc[0, "pm25"] == 187
    assert set(forecast["pollutant"]) == {"pm25", "uvi"}
    assert len(session.calls) == 1  # one request gives both


def test_geo_url_is_used_by_default(city, aq_config, fakes, aqicn_payload):
    session = fakes.Session([fakes.Response(200, aqicn_payload)])
    AQICNClient("TOKEN", city, aq_config, session).fetch_feed()
    assert "geo:31.5204;74.3587" in session.calls[0]["url"]


def test_pinned_station_url(city, aq_config, fakes, aqicn_payload):
    pinned = dataclasses.replace(aq_config, station_id="1234")
    session = fakes.Session([fakes.Response(200, aqicn_payload)])
    AQICNClient("TOKEN", city, pinned, session).fetch_feed()
    assert "/feed/@1234/" in session.calls[0]["url"]


def test_invalid_key_in_json_body(city, aq_config, fakes):
    session = fakes.Session([fakes.Response(200, {"status": "error", "data": "Invalid key"})])
    with pytest.raises(AuthenticationError):
        AQICNClient("BAD", city, aq_config, session).fetch_feed()


def test_over_quota(city, aq_config, fakes):
    session = fakes.Session([fakes.Response(200, {"status": "error", "data": "Over quota"})])
    with pytest.raises(RateLimitError):
        AQICNClient("TOKEN", city, aq_config, session).fetch_feed()


def test_other_api_error(city, aq_config, fakes):
    session = fakes.Session([fakes.Response(200, {"status": "error", "data": "Unknown station"})])
    with pytest.raises(APIError):
        AQICNClient("TOKEN", city, aq_config, session).fetch_feed()


def test_dash_reported_aqi_falls_back_to_computed_us_aqi(city, aq_config, fakes, aqicn_payload):
    # Reported "-" means no headline AQI, but the pollutant sub-indices still
    # give a valid US AQI (highest sub-index = pm25's 187).
    aqicn_payload["data"]["aqi"] = "-"
    session = fakes.Session([fakes.Response(200, aqicn_payload)])
    current, _ = AQICNClient("TOKEN", city, aq_config, session).fetch_feed()
    assert current.loc[0, "aqi"] == 187
    assert pd.isna(current.loc[0, "reported_aqi"])


def test_missing_forecast_is_fine(city, aq_config, fakes, aqicn_payload):
    del aqicn_payload["data"]["forecast"]
    session = fakes.Session([fakes.Response(200, aqicn_payload)])
    current, forecast = AQICNClient("TOKEN", city, aq_config, session).fetch_feed()
    assert len(current) == 1
    assert forecast.empty
