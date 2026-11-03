"""Server envelopes that must remain usable through every SDK."""

import httpx
import pytest

from originchain import OCBatchError, OCProtocolError
from originchain.models import _decode_sql_exec_response


@pytest.mark.parametrize("kind", ["insert", "update", "delete", "create_table", "begin", "commit"])
def test_statement_results_preserve_returning_and_unknown_fields(mock_client, kind):
    payload = {
        "kind": kind,
        "rows": [{"id": 9007199254740993, "amount": "1.00000000000000001"}],
        "rows_affected": 1,
        "returning": ["id", "amount"],
        "future_field": "kept",
    }

    def handler(request):
        assert request.url.path.endswith("/sql")
        assert b'"params":[7]' in request.content
        return httpx.Response(200, json=payload)

    result = mock_client(handler).sql.run("UPDATE items SET value=$1 RETURNING id, amount", [7])
    assert result.kind == kind
    assert result.rows == payload["rows"]
    assert result.columns == ["id", "amount"]
    assert result.rows_affected == 1
    assert result.raw["future_field"] == "kept"


def test_failed_batch_raises_with_partial_results_and_is_not_replayed(mock_client):
    calls = []
    payload = {
        "kind": "batch",
        "stopped_on_error": True,
        "results": [
            {"kind": "insert", "inserted": 1},
            {"error": "duplicate key", "sqlstate": "23505"},
        ],
    }

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload, headers={"X-OC-Request-Id": "batch-request"})

    with pytest.raises(OCBatchError) as caught:
        mock_client(handler).sql.execute(
            "INSERT INTO items VALUES (1); INSERT INTO items VALUES (1)"
        )
    assert len(calls) == 1
    assert caught.value.result.results[0].rows_affected == 1
    assert caught.value.result.results[1].raw["sqlstate"] == "23505"
    assert caught.value.request_id == "batch-request"


def test_successful_batch_preserves_each_result(mock_client):
    client = mock_client(
        lambda request: httpx.Response(
            200,
            json={
                "kind": "batch",
                "results": [
                    {"kind": "select", "rows": [{"one": 1}]},
                    {"kind": "update", "rows_affected": 2},
                ],
            },
        )
    )
    result = client.sql.run("SELECT 1; UPDATE items SET value=2")
    assert result.results[0].rows == [{"one": 1}]
    assert result.results[1].rows_affected == 2


@pytest.mark.parametrize(
    "payload", [[], {}, {"kind": "select", "rows": "bad"}, {"kind": "batch", "results": [4]}]
)
def test_malformed_envelope_is_a_protocol_error(payload):
    with pytest.raises(OCProtocolError):
        _decode_sql_exec_response(payload)
