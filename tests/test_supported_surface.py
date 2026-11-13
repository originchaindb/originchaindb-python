"""The next SDK exposes the four query families, without a natural-language endpoint."""
import pytest

from originchain import AsyncOriginChain, OriginChain


def test_sync_supported_query_surface():
    with OriginChain(url="https://example.com/v1/tenants/fixture", token="fixture") as client:
        assert not hasattr(client, "ask")
        assert callable(client.sql.query)
        assert callable(client.fts.search)
        assert callable(client.vector.topk)
        assert callable(client.vector_indexes.search)
        assert callable(client.graph.neighbors_of)


@pytest.mark.asyncio
async def test_async_supported_query_surface():
    async with AsyncOriginChain(url="https://example.com/v1/tenants/fixture", token="fixture") as client:
        assert not hasattr(client, "ask")
        assert callable(client.sql.query)
        assert callable(client.fts.search)
        assert callable(client.vector.topk)
        assert callable(client.vector_indexes.search)
        assert callable(client.graph.neighbors_of)
