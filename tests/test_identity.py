import httpx
import pytest
from originchain import OriginChain, AsyncOriginChain


@pytest.mark.parametrize("client_type", [OriginChain, AsyncOriginChain])
@pytest.mark.parametrize("kwargs", [dict(db_user="alice"), dict(db_key="secret"),
    dict(db_user="", db_key="secret"), dict(db_user="alice", db_key="bad\r\nkey")])
def test_incomplete_or_invalid_identity_rejected(client_type, kwargs):
    with pytest.raises(ValueError, match="db_user and db_key"):
        client_type(base_url="https://engine.invalid", bearer="bearer", tenant="tenant", **kwargs)


def test_sync_identity_is_sent_with_bearer_on_reads_and_writes(monkeypatch):
    requests = []
    real = httpx.Client
    def factory(**kwargs):
        def reply(request):
            requests.append(request)
            return httpx.Response(200, json={"ok": True})
        return real(**kwargs, transport=httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "Client", factory)
    with OriginChain(base_url="https://engine.invalid", bearer="bearer", tenant="tenant",
                     db_user="alice", db_key="secret") as client:
        client.health()
        client.rows.put("demo", {"id": "1"})
    assert len(requests) == 2
    for request in requests:
        assert request.headers["Authorization"] == "Bearer bearer"
        assert request.headers["X-OC-DB-User"] == "alice"
        assert request.headers["X-OC-DB-Key"] == "secret"
    assert requests[1].headers["Idempotency-Key"]


@pytest.mark.asyncio
async def test_async_identity_and_bearer_only_remain_separate(monkeypatch):
    requests = []
    real = httpx.AsyncClient
    def factory(**kwargs):
        def reply(request):
            requests.append(request)
            return httpx.Response(200, json={"ok": True})
        return real(**kwargs, transport=httpx.MockTransport(reply))
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    for kwargs in [dict(db_user="alice", db_key="secret"), {}]:
        async with AsyncOriginChain(base_url="https://engine.invalid", bearer="bearer",
                                    tenant="tenant", **kwargs) as client:
            await client.health()
    assert requests[0].headers["X-OC-DB-Key"] == "secret"
    assert "X-OC-DB-User" not in requests[1].headers
    assert "X-OC-DB-Key" not in requests[1].headers
