"""Actual SQL DELETE response variants through both client transports."""

import json

import httpx
import pytest

from originchain import SqlDelete


@pytest.mark.parametrize(
    "pk_fields,expected_pk",
    [({}, None), ({"pk": None}, None), ({"pk": "row-1"}, "row-1")],
    ids=["bulk-without-pk", "null-pk", "single-key-compatible"],
)
@pytest.mark.parametrize("count", [0, 2, 2**40, None])
def test_delete_decodes_optional_key_and_preserves_actual_count(
    mock_client, pk_fields, expected_pk, count,
):
    payload = {"kind": "delete", "schema": "demo.items", **pk_fields}
    if count is not None:
        payload["rows_affected"] = count

    def handler(request):
        assert request.method == "POST"
        assert request.url.path.endswith("/sql")
        assert json.loads(request.content) == {"sql": "DELETE FROM demo.items"}
        return httpx.Response(200, json=payload)

    client = mock_client(handler)
    tagged = client.sql("DELETE FROM demo.items")
    assert isinstance(tagged, SqlDelete)
    assert tagged.schema == "demo.items"
    assert tagged.pk == expected_pk
    assert tagged.rows_affected == count
    executed = client.sql.execute("DELETE FROM demo.items")
    assert executed.kind == "delete"
    assert executed.rows_affected == count
    assert executed.rows_buffered is None


@pytest.mark.parametrize("pk_fields", [{}, {"pk": None}])
def test_bulk_buffered_delete_does_not_claim_committed_rows(mock_client, pk_fields):
    payload = {"kind": "delete", "schema": "demo.items", "rows_buffered": 2, **pk_fields}
    client = mock_client(lambda _: httpx.Response(200, json=payload))
    tagged = client.sql("DELETE FROM demo.items")
    assert tagged.pk is None
    assert tagged.rows_buffered == 2
    assert tagged.rows_affected is None
    executed = client.sql.execute("DELETE FROM demo.items")
    assert executed.rows_buffered == 2
    assert executed.rows_affected is None
