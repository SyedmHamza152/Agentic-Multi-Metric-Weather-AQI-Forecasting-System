"""Shared test helpers: fake HTTP responses (so tests never call the real APIs) and sample payloads."""
import types

import pytest

from agentweather.config.settings import AQICNConfig, CityConfig, OpenWeatherConfig


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    """Returns queued responses in order; an Exception in the queue is raised instead."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def fakes():
    return types.SimpleNamespace(Response=FakeResponse, Session=FakeSession)


@pytest.fixture
def city():
    return CityConfig("Lahore", "PK", 31.5204, 74.3587, "Asia/Karachi")


@pytest.fixture
def ow_config():
    return OpenWeatherConfig("https://api.openweathermap.org", 10, 3, "metric", True)


@pytest.fixture
def aq_config():
    return AQICNConfig("https://api.waqi.info", 10, 3, None)


@pytest.fixture
def ow_weather_payload():
    return {
        "dt": 1731556800,
        "main": {"temp": 21.4, "humidity": 55, "pressure": 1015},
        "wind": {"speed": 2.1, "deg": 280},
        "clouds": {"all": 10},
        "rain": {"1h": 0.3},
        "weather": [{"description": "haze"}],
    }


@pytest.fixture
def ow_air_payload():
    return {
        "list": [
            {
                "dt": 1731556800,
                "main": {"aqi": 4},
                "components": {"co": 900.5, "no": 1.2, "no2": 30.1, "o3": 12.0,
                               "so2": 8.0, "pm2_5": 120.4, "pm10": 180.2, "nh3": 5.5},
            }
        ]
    }


@pytest.fixture
def aqicn_payload():
    return {
        "status": "ok",
        "data": {
            "aqi": 187,
            "idx": 1234,
            "dominentpol": "pm25",
            "city": {"name": "Lahore US Consulate", "geo": [31.55, 74.34]},
            "iaqi": {
                "pm25": {"v": 187}, "pm10": {"v": 95}, "o3": {"v": 10},
                "t": {"v": 20.5}, "h": {"v": 60}, "p": {"v": 1016}, "w": {"v": 1.5},
            },
            "time": {"v": 1731556800, "iso": "2024-11-14T05:00:00+05:00"},
            "forecast": {
                "daily": {
                    "pm25": [{"avg": 170, "day": "2024-11-14", "min": 120, "max": 200}],
                    "uvi": [{"avg": 2, "day": "2024-11-14", "min": 0, "max": 5}],
                }
            },
        },
    }
