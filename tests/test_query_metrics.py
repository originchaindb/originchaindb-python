"""Run query-metrics wire contracts through both real client transports."""

import inspect
import json
from pathlib import Path

import httpx
import pytest

from originchain import AsyncOriginChain, OCAuthError, OCServerError, OCValidationError


def payload():
    return json.loads((Path(__file__).parent / "fixtures/query-metrics.json").read_text())


@pytest.mark.parametrize("window", [None, (60, 60), (1440, 600), (10080, 3600)])
def test_metrics_preserves_wide_integers_null_and_wire_keys(mock_client, window):
    body = payload()
    # Python integers must also remain exact above JavaScript's safe range.
    body["buckets"][1]["calls"] = 2**53 + 17
    body["future_field"] = "preserved"

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/tenants/01HX1TESTTENANTXXXXXXXXXX1/query-metrics"
        minutes, seconds = window or (60, 60)
        assert dict(request.url.params) == {"minutes": str(minutes), "bucket_seconds": str(seconds)}
        assert request.headers["Authorization"] == "Bearer test-bearer"
        assert request.headers["User-Agent"] == "originchaindb-python/0.11.0rc1"
        assert request.headers["x-oc-logical-request-id"]
        assert request.headers["x-oc-attempt"] == "1"
        assert not request.content
        return httpx.Response(200, json=body)

    client = mock_client(handler)
    result = client.query_metrics(*(window or ()))
    assert result == body
    assert result["from"] == 1790000000000
    assert result["dropped_events"] == 4294967300
    assert result["buckets"][0]["mean_latency_ms"] is None
    assert result["buckets"][1]["mean_latency_ms"] == 12.5
    assert type(result["buckets"][1]["calls"]) is int
    assert result["buckets"][1]["calls"] == 2**53 + 17


@pytest.mark.parametrize("minutes,seconds", [(0, 60), (-1, 60), (True, 60), (1.5, 60), (60, False), (60, "60"), (60, 0)])
def test_invalid_scalar_parameters_do_not_send(mock_client, minutes, seconds):
    def handler(request):
        pytest.fail("invalid parameters reached transport")

    with pytest.raises(ValueError, match="positive integer"):
        mock_client(handler).query_metrics(minutes, seconds)


@pytest.mark.parametrize("status,error_type", [(400, OCValidationError), (401, OCAuthError), (403, OCAuthError), (503, OCServerError)])
def test_metrics_preserves_server_failures_and_request_ids(mock_client, status, error_type):
    body = {"error": {"code": "query_metrics_refused", "message": "request refused"}}

    def handler(request):
        # Unsupported pairs still reach the service, which owns this contract.
        assert dict(request.url.params) == {"minutes": "61", "bucket_seconds": "60"}
        return httpx.Response(status, json=body, headers={"x-oc-request-id": "a" * 28})

    with pytest.raises(error_type) as exc:
        mock_client(handler).query_metrics(61, 60)
    assert exc.value.status == status
    assert exc.value.body == body
    assert exc.value.request_id == "a" * 28
    assert exc.value.logical_request_id


def test_async_metrics_is_a_native_coroutine():
    assert inspect.iscoroutinefunction(AsyncOriginChain.query_metrics)
