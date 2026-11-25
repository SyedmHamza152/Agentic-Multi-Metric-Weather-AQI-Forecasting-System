import dataclasses

from agentweather.config.settings import load_settings
from agentweather.pipelines import ingestion_pipeline as pipeline
from agentweather.store.csv_store import CSVStore


def _settings(tmp_path, monkeypatch, with_keys):
    for name in ("OPENWEATHER_API_KEY", "AQICN_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    if with_keys:
        monkeypatch.setenv("OPENWEATHER_API_KEY", "KEY")
        monkeypatch.setenv("AQICN_API_TOKEN", "TOKEN")
    # env_file points to a file that does not exist, so a real .env on your PC is never loaded in tests
    settings = load_settings(env_file=tmp_path / "no.env")
    storage = dataclasses.replace(settings.storage, raw_dir=tmp_path / "raw")
    return dataclasses.replace(settings, storage=storage)


def test_settings_load_from_yaml(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch, with_keys=False)
    assert s.city.name == "Lahore"
    assert len(s.targets) == 6
    assert "KEY" not in repr(s)


def test_missing_keys_fail_jobs_but_do_not_crash(tmp_path, monkeypatch):
    s = _settings(tmp_path, monkeypatch, with_keys=False)
    results = pipeline.run_current(s, CSVStore(s.storage.raw_dir))
    assert len(results) == 3
    assert not any(r.ok for r in results)
    assert all("not set" in r.error for r in results)


def test_one_failing_job_does_not_stop_the_others(tmp_path, monkeypatch, fakes, aqicn_payload):
    s = _settings(tmp_path, monkeypatch, with_keys=True)
    store = CSVStore(s.storage.raw_dir)

    # AQICN works, both OpenWeather jobs get HTTP 500
    real_aqicn = pipeline.AQICNClient

    def aqicn_with_fake_session(token, city, config):
        return real_aqicn(token, city, config, session=fakes.Session([fakes.Response(200, aqicn_payload)]))

    def openweather_with_fake_session(key, city, config):
        return pipeline.OpenWeatherClient(
            key, city, config, session=fakes.Session([fakes.Response(500, None, "boom")] * 3)
        )

    monkeypatch.setattr(pipeline, "AQICNClient", aqicn_with_fake_session)
    monkeypatch.setattr(pipeline, "OpenWeatherClient", openweather_with_fake_session)

    results = {r.name: r for r in pipeline.run_current(s, store)}
    assert results["aqicn_air_and_forecast"].ok
    assert not results["openweather_weather"].ok
    assert store.exists("aqicn_air")
    assert pipeline._summarize(list(results.values())) == 1  # exit code signals the failure
