"""Wire contracts for projected rows, temporal precision and typed specials."""
from __future__ import annotations

from copy import deepcopy
import json
import math

import httpx
import pytest

from originchain import SqlSelect
from originchain.models import _decode_sql_response, _decode_sql_result


def sql_rows(client, method, query="SELECT * FROM demo.values"):
    if method == "query":
        return client.sql.query(query).rows
    if method == "tagged":
        return list(client.sql(query).rows)
    row = client.sql_one(query)
    return [] if row is None else [row]


@pytest.mark.parametrize("method", ["query", "tagged"])
@pytest.mark.parametrize("columns,rows", [
    (["n"], [{"n": 1}, {"n": 2}]),
    (["s"], [{"s": "7.25"}, {"s": "12.75"}]),
    (["grp", "n"], [{"grp": "g1", "n": 2}, {"grp": "g2", "n": 1}]),
    # Older engines may include an unprojected key. Do not silently discard data.
    (["n"], [{"grp": "g2", "n": 1}, {"grp": "g1", "n": 2}]),
])
def test_group_projection_preserves_exact_server_keys(mock_client, method, columns, rows):
    payload = {"kind": "select", "columns": columns, "rows": rows}
    client = mock_client(lambda _: httpx.Response(200, json=payload))
    assert sql_rows(client, method, "SELECT COUNT(*) AS n FROM demo.values GROUP BY grp") == rows
    if method == "query":
        assert client.sql.query("SELECT ...").columns == columns


@pytest.mark.parametrize("rows", [[{"n": 2}, {"n": 1}], [{"grp": "g1", "n": 2}]])
def test_materialized_view_does_not_recreate_or_strip_group_keys(mock_client, rows):
    def handler(request):
        assert request.method == "GET" and request.url.path.endswith("/sql/materialized-views/mv_count")
        return httpx.Response(200, json={"name": "mv_count", "rows": rows})
    client = mock_client(handler)
    assert list(client.sql.read_materialized_view("mv_count").rows) == rows


@pytest.mark.parametrize("method", ["query", "tagged", "one"])
def test_temporal_and_decimal_values_keep_their_wire_precision(mock_client, method):
    row = {"ts": "2024-02-29 12:34:56.123456", "tz": "2024-02-29T12:34:56.123456Z",
           "duration": "36:00:00.000001", "calendar": "1 year 2 mons 3 days 04:05:06",
           "amount": "12345678901234567890.1234567890", "old_ts": 1709210096123,
           "old_duration": 129600000, "optional": None}
    types = {"ts": "timestamp", "tz": "timestamptz", "duration": "interval",
             "calendar": "interval", "amount": "numeric"}
    client = mock_client(lambda _: httpx.Response(200, json={
        "kind": "select", "rows": [row], "column_types": types,
    }))
    decoded = sql_rows(client, method)[0]
    assert decoded == row
    assert all(type(decoded[key]) is type(value) for key, value in row.items())


@pytest.mark.parametrize("row", [
    {"ts": "2024-02-29T12:34:56.123456Z", "duration": "36:00:00.000001"},
    {"ts": 1709210096123, "duration": 129600000},
    {"number": "NaN", "text": "Infinity", "ts": "infinity", "duration": "-infinity"},
])
def test_row_get_keeps_untyped_values_unchanged(mock_client, row):
    def handler(request):
        assert request.method == "GET" and request.url.path.endswith("/rows/demo.values/one")
        return httpx.Response(200, json=row)
    assert mock_client(handler).rows.get("demo.values", "one") == row


@pytest.mark.parametrize("value", [1709210096123, "2024-02-29T12:34:56.123456Z"])
def test_timestamp_writes_keep_millisecond_numbers_and_microsecond_text(mock_client, value):
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"kind": "insert", "schema": "demo.values", "inserted": 1})
    client = mock_client(handler)
    client.rows.put("demo.values", {"id": "one", "ts": value})
    client.sql("INSERT INTO demo.values (id, ts) VALUES ($1, $2)", ["two", value])
    assert bodies == [{"id": "one", "ts": value},
                      {"sql": "INSERT INTO demo.values (id, ts) VALUES ($1, $2)", "params": ["two", value]}]


@pytest.mark.parametrize("method", ["query", "tagged", "one"])
@pytest.mark.parametrize("float_type", ["double precision", "real", "float4", "float8"])
def test_only_explicit_float_specials_become_python_floats(mock_client, method, float_type):
    row = {"nan": "NaN", "pos": "Infinity", "neg": "-Infinity", "finite": 1.25,
           "zero": -0.0, "missing": None, "numeric": "NaN", "text": "Infinity",
           "numeric_pos": "Infinity", "numeric_neg": "-Infinity", "amount": "12.30",
           "date": "infinity", "timestamp": "-infinity", "tz": "infinity",
           "interval": "1 year 2 mons 3 days 04:05:06", "interval_inf": "-infinity",
           "ordinary_date": "2024-02-29"}
    types = {key: float_type for key in ["nan", "pos", "neg", "finite", "zero", "missing"]}
    types.update(numeric="numeric", numeric_pos="decimal", numeric_neg="numeric", amount="numeric",
                 text="text", date="date", timestamp="timestamp", tz="timestamptz",
                 interval="interval", interval_inf="interval", ordinary_date="date")
    client = mock_client(lambda _: httpx.Response(200, json={"kind": "select", "rows": [row], "column_types": types}))
    result = sql_rows(client, method)[0]
    assert isinstance(result["nan"], float) and math.isnan(result["nan"])
    assert result["pos"] == math.inf and result["neg"] == -math.inf
    for key in set(row) - {"nan", "pos", "neg"}:
        assert result[key] == row[key] and type(result[key]) is type(row[key])
    assert math.copysign(1, result["zero"]) == -1


@pytest.mark.parametrize("metadata", [None, {}, {"value": "text"}, {"value": "numeric"},
                                      {"value": "unknown_future_type"}, {"value": "float8[]"},
                                      {"other": "float8"}, ["float8"], {"value": None}])
@pytest.mark.parametrize("method", ["query", "tagged", "one"])
def test_absent_unknown_or_malformed_metadata_never_guesses_float(mock_client, method, metadata):
    row = {"value": "NaN", "nested": {"value": "Infinity"}, "array": ["-Infinity"]}
    payload = {"kind": "select", "rows": [row]}
    if metadata is not None:
        payload["column_types"] = metadata
    client = mock_client(lambda _: httpx.Response(200, json=payload))
    assert sql_rows(client, method) == [row]


@pytest.mark.parametrize("metadata", [None, {}, {"ts": "timestamp", "future": "custom_type"}])
def test_result_exposes_optional_column_types_even_without_rows(mock_client, metadata):
    payload = {"kind": "select", "columns": ["ts"], "rows": []}
    if metadata is not None:
        payload["column_types"] = metadata
    client = mock_client(lambda _: httpx.Response(200, json=payload))
    result = client.sql.query("SELECT ts FROM demo.values WHERE false")
    assert result.rows == [] and result.columns == ["ts"] and result.column_types == metadata
    tagged = client.sql("SELECT ts FROM demo.values WHERE false")
    assert isinstance(tagged, SqlSelect) and tagged.rows == () and tagged.column_types == metadata


def test_decoding_does_not_mutate_the_payload_or_derive_missing_projection_keys():
    payload = {"kind": "select", "columns": ["special"], "column_types": {"special": "float8", "absent": "float8"},
               "rows": [{"special": "NaN"}]}
    original = deepcopy(payload)
    for decode in [_decode_sql_response, _decode_sql_result]:
        result = decode(payload)
        assert math.isnan(result.rows[0]["special"])
        assert set(result.rows[0]) == {"special"}
        result.column_types["special"] = "text"
        assert payload == original
