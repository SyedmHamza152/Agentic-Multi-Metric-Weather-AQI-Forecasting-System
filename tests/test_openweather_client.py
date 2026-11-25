from datetime import datetime, timedelta, timezone

import pytest
import requests

from agentweather.ingestion.openweather_client import OpenWeatherClient, parse_current_weather
from agentweather.utils.exceptions import (
    APIError,
    AuthenticationError,
    ConfigError,
    DataValidationError,
    FeatureUnavailableError,
    RateLimitError,
)


def test_parse_current_weather(city, ow_weather_payload):
    row = parse_current_weather(ow_weather_payload, city)
    assert row["temperature_c"] == 21.4
    assert row["precipitation_mm"] == 0.3
    assert row["timestamp_utc"].endswith("Z")


def test_parse_bad_shape_raises(city):
    with pytest.raises(DataValidationError):
        parse_current_weather({"unexpected": True}, city)


def test_missing_key_is_a_config_error(city, ow_config):
    with pytest.raises(ConfigError):
        OpenWeatherClient("", city, ow_config)


def test_current_weather_includes_uv(city, ow_config, fakes, ow_weather_payload):
    session = fakes.Session([
        fakes.Response(200, ow_weather_payload),
        fakes.Response(200, {"current": {"uvi": 3.4}}),
    ])
    df = OpenWeatherClient("KEY", city, ow_config, session).fetch_current_weather()
    assert df.loc[0, "uv_index"] == 3.4
    assert session.calls[0]["timeout"] == 10  # timeout is always passed


def test_uv_failure_does_not_lose_weather(city, ow_config, fakes, ow_weather_payload):
    session = fakes.Session([
        fakes.Response(200, ow_weather_payload),
        fakes.Response(401, {"message": "needs subscription"}),
    ])
    df = OpenWeatherClient("KEY", city, ow_config, session).fetch_current_weather()
    assert len(df) == 1
    assert df["uv_index"].isna().all()


def test_invalid_key_raises_authentication_error(city, ow_config, fakes):
    session = fakes.Session([fakes.Response(401, {"cod": 401})])
    with pytest.raises(AuthenticationError):
        OpenWeatherClient("BAD", city, ow_config, session).fetch_current_weather()


def test_rate_limit_raises(city, ow_config, fakes):
    session = fakes.Session([fakes.Response(429, {})])
    with pytest.raises(RateLimitError):
        OpenWeatherClient("KEY", city, ow_config, session).fetch_current_weather()


def test_timeout_raises_api_error(city, ow_config, fakes):
    session = fakes.Session([requests.exceptions.Timeout("slow")])
    with pytest.raises(APIError, match="timed out"):
        OpenWeatherClient("KEY", city, ow_config, session).fetch_current_weather()


def test_network_error_message_never_contains_key(city, ow_config, fakes):
    err = requests.exceptions.ConnectionError("failed: https://x/?appid=SECRET123&lat=1")
    session = fakes.Session([err])
    with pytest.raises(APIError) as info:
        OpenWeatherClient("SECRET123", city, ow_config, session).fetch_current_weather()
    assert "SECRET123" not in str(info.value)


def test_invalid_json_raises(city, ow_config, fakes):
    session = fakes.Session([fakes.Response(200, None)])
    with pytest.raises(APIError, match="JSON"):
        OpenWeatherClient("KEY", city, ow_config, session).fetch_current_weather()


def test_current_air_pollution(city, ow_config, fakes, ow_air_payload):
    session = fakes.Session([fakes.Response(200, ow_air_payload)])
    df = OpenWeatherClient("KEY", city, ow_config, session).fetch_current_air_pollution()
    assert df.loc[0, "pm2_5"] == 120.4
    assert df.loc[0, "ow_aqi"] == 4


def test_air_history_is_fetched_in_chunks(city, ow_config, fakes, ow_air_payload):
    # 65 days -> 3 requests of at most 30 days
    session = fakes.Session([fakes.Response(200, ow_air_payload) for _ in range(3)])
    end = datetime.now(timezone.utc)
    OpenWeatherClient("KEY", city, ow_config, session).fetch_air_pollution_history(
        end - timedelta(days=65), end
    )
    assert len(session.calls) == 3


def test_timemachine_auth_error_becomes_feature_unavailable(city, ow_config, fakes):
    session = fakes.Session([fakes.Response(401, {})])
    client = OpenWeatherClient("KEY", city, ow_config, session)
    with pytest.raises(FeatureUnavailableError):
        client.fetch_weather_history([datetime.now(timezone.utc)])


def test_timemachine_respects_max_calls(city, ow_config, fakes):
    payload = {"data": [{"dt": 1731556800, "temp": 20, "humidity": 50, "wind_speed": 2, "uvi": 1}]}
    session = fakes.Session([fakes.Response(200, payload) for _ in range(2)])
    now = datetime.now(timezone.utc)
    stamps = [now - timedelta(hours=h) for h in range(5)]
    OpenWeatherClient("KEY", city, ow_config, session).fetch_weather_history(stamps, max_calls=2)
    assert len(session.calls) == 2
