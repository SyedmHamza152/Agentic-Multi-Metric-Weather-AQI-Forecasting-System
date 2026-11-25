import pandas as pd

from agentweather.store.csv_store import CSVStore


def _df(*rows):
    return pd.DataFrame(rows, columns=["timestamp_utc", "station_id", "aqi"])


def test_save_then_load(tmp_path):
    store = CSVStore(tmp_path)
    assert not store.exists("demo")
    added = store.save("demo", _df(("2024-01-01T00:00:00Z", "1", 100)), ["timestamp_utc", "station_id"])
    assert added == 1
    assert store.exists("demo")
    assert len(store.load("demo")) == 1


def test_duplicate_key_is_replaced_not_added(tmp_path):
    store = CSVStore(tmp_path)
    keys = ["timestamp_utc", "station_id"]
    store.save("demo", _df(("2024-01-01T00:00:00Z", "1", 100)), keys)
    added = store.save("demo", _df(("2024-01-01T00:00:00Z", "1", 150)), keys)
    out = store.load("demo")
    assert added == 0
    assert len(out) == 1
    assert out.loc[0, "aqi"] == 150  # newest value wins


def test_new_rows_are_appended_and_sorted(tmp_path):
    store = CSVStore(tmp_path)
    keys = ["timestamp_utc", "station_id"]
    store.save("demo", _df(("2024-01-02T00:00:00Z", "1", 1)), keys)
    store.save("demo", _df(("2024-01-01T00:00:00Z", "1", 2)), keys)
    out = store.load("demo")
    assert list(out["timestamp_utc"]) == ["2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z"]


def test_numeric_station_ids_match_text_ids(tmp_path):
    store = CSVStore(tmp_path)
    keys = ["timestamp_utc", "station_id"]
    store.save("demo", _df(("2024-01-01T00:00:00Z", "1234", 1)), keys)
    added = store.save("demo", _df(("2024-01-01T00:00:00Z", 1234, 1)), keys)
    assert added == 0


def test_empty_dataframe_is_ignored(tmp_path):
    store = CSVStore(tmp_path)
    assert store.save("demo", pd.DataFrame(), ["timestamp_utc"]) == 0
    assert not store.exists("demo")


def test_no_temp_file_left_behind(tmp_path):
    store = CSVStore(tmp_path)
    store.save("demo", _df(("2024-01-01T00:00:00Z", "1", 1)), ["timestamp_utc", "station_id"])
    assert [p.name for p in tmp_path.iterdir()] == ["demo.csv"]
