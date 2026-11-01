"""OC_URL + OC_TOKEN: the one way a client reaches its database.

OC_URL is opaque. Every test that sends a request runs with both address
shapes: the one Connect shows today (with a database id in the path) and one
without it. The client must append door paths to either exactly as given.
"""
from __future__ import annotations

import asyncio
import json
import warnings
from typing import Any, Callable, Iterator

import httpx
import pytest

from originchain import AsyncOriginChain, OCConfigError, OCError, OriginChain
from originchain import _connect

TOKEN = "oc_pat_fixture.Token-value_123"
WITH_ID = "https://db.example.com/v1/tenants/01hx1testtenantxxxxxxxxxx1"
WITHOUT_ID = "https://db.example.com/v1"
OPAQUE = "https://db-7.example.net:8443/some/opaque/prefix"
SHAPES = [WITH_ID, WITHOUT_ID, OPAQUE]
NEW_ENV = ("OC_URL", "OC_TOKEN")
OLD_ENV = ("OC_BASE_URL", "OC_TENANT", "OC_BEARER")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in NEW_ENV + OLD_ENV + ("OC_DB_USER", "OC_DB_KEY"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., list[httpx.Request]]]:
    """Route both clients' HTTP through a recording MockTransport."""
    seen: list[httpx.Request] = []

    def install(reply: Any = None) -> list[httpx.Request]:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if reply is not None:
                return reply(request)
            return httpx.Response(200, json={"kind": "select", "columns": ["one"],
                                             "rows": [{"one": 1}]})

        real_sync, real_async = httpx.Client, httpx.AsyncClient
        monkeypatch.setattr(httpx, "Client", lambda **kw: real_sync(
            **kw, transport=httpx.MockTransport(handler)))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real_async(
            **kw, transport=httpx.MockTransport(handler)))
        return seen

    yield install


def _sql(client: Any) -> Any:
    if isinstance(client, AsyncOriginChain):
        async def run() -> Any:
            async with client:
                return await client.sql.query("SELECT $1 AS one", [1])
        return asyncio.run(run())
    with client:
        return client.sql.query("SELECT $1 AS one", [1])


def _close(client: Any) -> None:
    if isinstance(client, AsyncOriginChain):
        asyncio.run(client.aclose())
    else:
        client.close()


# ── from_env reads OC_URL + OC_TOKEN, and needs nothing else ──────────────


@pytest.mark.parametrize("cls", [OriginChain, AsyncOriginChain])
@pytest.mark.parametrize("address", SHAPES)
def test_from_env_needs_only_oc_url_and_oc_token(cls, address, clean_env, transport):
    seen = transport()
    clean_env.setenv("OC_URL", address)
    clean_env.setenv("OC_TOKEN", TOKEN)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # negative control: the new names never warn
        result = _sql(cls.from_env())
    assert result.rows == [{"one": 1}]
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == address + "/sql"
    assert request.method == "POST"
    assert json.loads(request.content) == {"sql": "SELECT $1 AS one", "params": [1]}
    # The token is sent unchanged, and it is the only credential.
    assert request.headers["Authorization"] == "Bearer " + TOKEN
    assert not [h for h in request.headers if h.lower().startswith("x-oc-db-")]
    assert "x-oc-tenant" not in request.headers


@pytest.mark.parametrize("cls", [OriginChain, AsyncOriginChain])
@pytest.mark.parametrize("missing", NEW_ENV)
def test_from_env_names_the_missing_value(cls, missing, clean_env, transport):
    seen = transport()
    for name in NEW_ENV:
        if name != missing:
            clean_env.setenv(name, WITH_ID if name == "OC_URL" else TOKEN)
    with pytest.raises(OCConfigError, match=f"missing required env var: {missing}"):
        cls.from_env()
    assert seen == []


@pytest.mark.parametrize("address", SHAPES)
def test_every_door_is_appended_to_the_address(address, transport):
    seen = transport(lambda r: httpx.Response(200, json=[] if r.method == "GET" else {"kind": "select", "rows": []}))
    with OriginChain(url=address, token=TOKEN, max_retries=0) as db:
        db.sql.query("SELECT 1")
        db.cypher("MATCH (n) RETURN n", {"x": 1})
        db.schemas.list()
        db.graph.neighbors("demo.nodes", rel="knows", pk="a")
        db.fts_search("demo.docs", "body", q="tea")
    paths = [str(r.url).split("?")[0] for r in seen]
    assert paths == [
        address + "/sql",
        address + "/cypher",
        address + "/schemas",
        address + "/graph/demo.nodes/neighbors",
        address + "/fts/demo.docs/body",
    ]


@pytest.mark.parametrize("address", SHAPES)
def test_async_doors_are_appended_to_the_address(address, transport):
    seen = transport(lambda r: httpx.Response(200, json=[] if r.method == "GET" else {"kind": "select", "rows": []}))

    async def run() -> None:
        async with AsyncOriginChain(url=address, token=TOKEN, max_retries=0) as db:
            await db.sql.query("SELECT 1")
            await db.cypher("MATCH (n) RETURN n")
            await db.schemas.list()

    asyncio.run(run())
    assert [str(r.url) for r in seen] == [address + "/sql", address + "/cypher", address + "/schemas"]


@pytest.mark.parametrize("cls", [OriginChain, AsyncOriginChain])
def test_routes_outside_the_database_use_only_the_origin(cls, transport):
    seen = transport(lambda r: httpx.Response(200, json={"status": "ok"}))
    client = cls(url=OPAQUE, token=TOKEN)
    if isinstance(client, AsyncOriginChain):
        async def run() -> None:
            async with client:
                await client.health()
        asyncio.run(run())
    else:
        with client:
            client.health()
    assert str(seen[0].url) == "https://db-7.example.net:8443/health"


def test_a_trailing_slash_does_not_double_the_separator(transport):
    seen = transport()
    with OriginChain(url=WITH_ID + "/", token=TOKEN) as db:
        db.sql.query("SELECT 1")
    assert str(seen[0].url) == WITH_ID + "/sql"


def test_cypher_binds_by_name_and_returns_the_response_unchanged(transport):
    body = {"kind": "select", "rows": [{"n.name": "a"}]}
    seen = transport(lambda r: httpx.Response(200, json=body))
    with OriginChain(url=WITHOUT_ID, token=TOKEN) as db:
        assert db.cypher("MATCH (n) WHERE n.id = $id RETURN n.name", {"id": 7},
                         default_schema="demo.nodes") == body
        with pytest.raises(OCError, match="mapping of names"):
            db.cypher("MATCH (n) RETURN n", [7])  # type: ignore[arg-type]
    assert len(seen) == 1
    assert json.loads(seen[0].content) == {
        "cypher": "MATCH (n) WHERE n.id = $id RETURN n.name",
        "params": {"id": 7}, "default_schema": "demo.nodes",
    }
    assert seen[0].headers["Idempotency-Key"]


# ── Plain http:// is refused except to this machine ───────────────────────

REFUSED = [
    "http://db.example.com/v1/tenants/x",       # positive control: must be refused
    "HTTP://db.example.com/v1",
    "http://10.0.0.5:8080/v1",
    "http://[2001:db8::1]/v1",
    "http://127.0.0.1.example.com/v1",
    "http://localhost.example.com/v1",
    "ftp://db.example.com/v1",
    "db.example.com/v1",
    "https://",
    "https://user:secret@db.example.com/v1",
    "https://db.example.com/v1?tenant=x",
    "https://db.example.com/v1?",
    "https://db.example.com/v1#x",
    "https://db.example.com/v1\n",
    " https://db.example.com/v1",
    "https://db.example.com:notaport/v1",
    "",
]
ALLOWED = [
    "http://localhost:8080/v1/tenants/x",       # negative controls: loopback is exempt
    "http://LOCALHOST:8080/v1",
    "http://127.0.0.1:8080/v1",
    "http://127.5.6.7/v1",
    "http://[::1]:8080/v1",
    "https://db.example.com",
    "https://db.example.com/v1/tenants/x",
]


@pytest.mark.parametrize("address", REFUSED)
def test_check_url_refuses(address):
    with pytest.raises(OCConfigError):
        _connect.check_url(address)


@pytest.mark.parametrize("address", ALLOWED)
def test_check_url_allows(address):
    assert _connect.check_url(address) == address


def test_plain_http_refusal_says_why_without_echoing_the_token():
    with pytest.raises(OCConfigError, match=r"plain http://.*localhost, 127\.0\.0\.1 or ::1") as caught:
        _connect.check_url("http://db.example.com/v1")
    assert isinstance(caught.value, OCError) and isinstance(caught.value, ValueError)


@pytest.mark.parametrize("cls", [OriginChain, AsyncOriginChain])
@pytest.mark.parametrize("address", ["http://db.example.com/v1/tenants/x", "http://db.example.com/v1"])
def test_http_address_is_refused_before_any_request(cls, address, clean_env, transport):
    seen = transport()
    with pytest.raises(OCConfigError, match="plain http://"):
        cls(url=address, token=TOKEN)
    clean_env.setenv("OC_URL", address)
    clean_env.setenv("OC_TOKEN", TOKEN)
    with pytest.raises(OCConfigError, match="plain http://"):
        cls.from_env()
    assert seen == []


@pytest.mark.parametrize("cls", [OriginChain, AsyncOriginChain])
def test_loopback_http_is_allowed(cls, transport):
    seen = transport()
    _sql(cls(url="http://127.0.0.1:8080/v1/tenants/x", token=TOKEN))
    assert str(seen[0].url) == "http://127.0.0.1:8080/v1/tenants/x/sql"


def test_old_names_cannot_smuggle_plain_http(clean_env, transport):
    seen = transport()
    clean_env.setenv("OC_BASE_URL", "http://db.example.com")
    clean_env.setenv("OC_TENANT", "x")
    clean_env.setenv("OC_BEARER", TOKEN)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with pytest.raises(OCConfigError, match="plain http://"):
            OriginChain.from_env()
    assert seen == []


@pytest.mark.parametrize("token", ["", "abc def", "abc\n", "tokén"])
def test_unusable_tokens_are_refused_without_echoing_them(token):
    with pytest.raises(OCConfigError) as caught:
        OriginChain(url=WITH_ID, token=token)
    if token:
        assert token.strip() not in str(caught.value)


# ── Old names: read for one release, one-time DeprecationWarning ─────────


def _deprecations(record: list[warnings.WarningMessage]) -> list[str]:
    return [str(w.message) for w in record if issubclass(w.category, DeprecationWarning)]


@pytest.mark.parametrize("cls", [OriginChain, AsyncOriginChain])
def test_old_env_names_still_work_and_warn_once_naming_the_replacement(cls, clean_env, transport):
    seen = transport()
    clean_env.setenv("OC_BASE_URL", "https://db.example.com/")
    clean_env.setenv("OC_TENANT", "01hx1testtenantxxxxxxxxxx1")
    clean_env.setenv("OC_BEARER", TOKEN)
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")  # defeat the registry, so "once" is the SDK's doing
        first = cls.from_env()
        second = cls.from_env()
    _close(second)
    messages = _deprecations(record)
    assert len(messages) == 2, messages
    assert any("OC_BASE_URL and OC_TENANT are deprecated" in m and "set OC_URL" in m for m in messages)
    assert any("OC_BEARER is deprecated" in m and "set OC_TOKEN" in m for m in messages)
    assert not any(TOKEN in m for m in messages)
    # The warning points at the caller's line, so default filters show it there.
    assert all(w.filename == __file__ for w in record)
    assert first.url == second.url == WITH_ID
    _sql(first)
    assert str(seen[0].url) == WITH_ID + "/sql"
    assert seen[0].headers["Authorization"] == "Bearer " + TOKEN


def test_new_names_win_and_old_ones_are_reported_as_ignored(clean_env, transport):
    transport()
    clean_env.setenv("OC_URL", WITHOUT_ID)
    clean_env.setenv("OC_TOKEN", TOKEN)
    clean_env.setenv("OC_TENANT", "stale")
    clean_env.setenv("OC_BEARER", "stale-token")
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        db = OriginChain.from_env()
    messages = _deprecations(record)
    assert len(messages) == 2
    assert all("ignored" in m for m in messages)
    assert db.url == WITHOUT_ID and db.token == TOKEN
    db.close()


@pytest.mark.parametrize("cls", [OriginChain, AsyncOriginChain])
def test_old_constructor_arguments_still_work_and_warn_once(cls, transport):
    seen = transport()
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        first = cls(base_url="https://db.example.com", bearer=TOKEN, tenant="t1")
        _close(cls(base_url="https://db.example.com", bearer=TOKEN, tenant="t1"))
    messages = _deprecations(record)
    assert len(messages) == 2, messages
    assert any("base_url=..., tenant=...) is deprecated" in m and "pass url=" in m for m in messages)
    assert any("bearer=...) is deprecated" in m and "pass token=" in m for m in messages)
    assert all(w.filename == __file__ for w in record)
    assert first.url == "https://db.example.com/v1/tenants/t1"
    _sql(first)
    assert str(seen[0].url) == "https://db.example.com/v1/tenants/t1/sql"


@pytest.mark.parametrize("cls", [OriginChain, AsyncOriginChain])
def test_identity_arguments_are_deprecated_but_still_sent_for_one_release(cls, transport):
    seen = transport()
    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        client = cls(url=WITH_ID, token=TOKEN, db_user="alice", db_key="secret-key")
        _close(cls(url=WITH_ID, token=TOKEN, db_user="alice", db_key="secret-key"))
    messages = _deprecations(record)
    assert len(messages) == 1 and "db_user=..., db_key=...) is deprecated" in messages[0]
    assert "secret-key" not in messages[0]
    _sql(client)
    assert seen[0].headers["X-OC-DB-User"] == "alice"


def test_mixing_old_and_new_arguments_is_refused():
    with pytest.raises(OCConfigError, match="url= alone"):
        OriginChain(url=WITH_ID, token=TOKEN, tenant="t1")
    with pytest.raises(OCConfigError, match="token= alone"):
        OriginChain(url=WITH_ID, token=TOKEN, bearer=TOKEN)
    with pytest.raises(TypeError, match="'url'"):
        OriginChain(token=TOKEN)
    with pytest.raises(TypeError, match="'token'"):
        OriginChain(url=WITH_ID)
