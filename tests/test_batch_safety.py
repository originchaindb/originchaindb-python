"""Batch identities follow input chunks, independently of server row counts."""
import httpx
import pytest


def test_partial_insert_counts_do_not_reuse_a_chunk_key(mock_client):
    seen = []

    def handler(request):
        seen.append(request.headers["Idempotency-Key"])
        # Simulate a response count below the submitted chunk length.
        return httpx.Response(200, json={"inserted": 0})

    db = mock_client(handler)
    try:
        db.rows.put_batch("demo.rows", ({"id": i} for i in range(5)), chunk=2,
                          idempotency_key="stable")
        assert seen == ["stable-c0", "stable-c1", "stable-c2"]
    finally:
        db.close()


@pytest.mark.parametrize("chunk", [0, -1, 1.5, True])
def test_invalid_chunk_size_fails_before_consuming_rows(mock_client, chunk):
    def rows():
        pytest.fail("invalid batch configuration consumed its input")
        yield {}

    db = mock_client(lambda _: pytest.fail("invalid batch sent a request"))
    try:
        with pytest.raises(ValueError, match="positive integer"):
            db.rows.put_batch("demo.rows", rows(), chunk=chunk)
    finally:
        db.close()


def test_empty_batch_sends_nothing(mock_client):
    db = mock_client(lambda _: pytest.fail("empty batch sent a request"))
    try:
        assert db.rows.put_batch("demo.rows", [], chunk=2) == 0
    finally:
        db.close()
