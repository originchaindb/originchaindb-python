"""Transport-level retry contract; every case runs against sync and async."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest

from originchain import AsyncOriginChain, OriginChain
from originchain import _retry
from originchain._namespaces import _SqlNamespace
from originchain.errors import OCError, OCRateLimitedError

CONTRACT = json.loads((Path(__file__).parent / 'fixtures/retry-cases.json').read_text())


@pytest.mark.parametrize('case', CONTRACT['retry_after'])
def test_retry_after_contract(case):
    assert _retry.retry_after(case['value'], now=CONTRACT['now_unix']) == case['seconds']


@pytest.mark.parametrize('case', CONTRACT['methods'])
def test_method_contract(case):
    assert _retry.eligible(case['method'], case['retry_safe']) == case['eligible']


def test_status_contract():
    assert sorted(_retry.RETRYABLE_STATUSES) == CONTRACT['retryable_statuses']


@pytest.mark.parametrize('retries', [-1, 100, 1.5, True])
@pytest.mark.parametrize('cls', [OriginChain, AsyncOriginChain])
def test_invalid_retries_fail_before_transport(cls, retries):
    with pytest.raises(ValueError, match='max_retries'):
        cls(base_url='http://test.invalid', bearer='test', tenant='test', max_retries=retries)


@pytest.mark.parametrize('budget', [0, -1, float('inf'), float('nan'), True])
@pytest.mark.parametrize('cls', [OriginChain, AsyncOriginChain])
def test_invalid_budget(cls, budget):
    with pytest.raises(ValueError, match='max_retry_time'):
        cls(base_url='http://test.invalid', bearer='test', tenant='test', max_retry_time=budget)


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('method,safe,status,delay,attempts', [
    ('GET', None, 503, '0', 3),
    ('POST', None, 503, '0', 1),
    ('PUT', None, 503, '0', 1),
    ('DELETE', None, 503, '0', 1),
    ('POST', True, 503, '0', 3),
    ('GET', False, 503, '0', 1),
    ('GET', None, 401, '0', 1),
    ('GET', None, 429, '600', 1),
    ('GET', None, 429, '-1', 3),
])
def test_attempts_headers_and_budget(asynchronous, method, safe, status, delay, attempts):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={'error': 'busy'}, headers={'Retry-After': delay})

    cls = AsyncOriginChain if asynchronous else OriginChain
    # Avoid unrelated TLS initialization: exercise the real request loop on MockTransport.
    db = cls.__new__(cls)
    db.max_retries, db.max_retry_time, db._diagnostics = 2, 0.5, None
    db._backoff = lambda attempt: 0
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient if asynchronous else httpx.Client
    db._client = client(base_url='http://test.invalid', transport=transport)

    async def run():
        try:
            await db._request(method, '/health', retry_safe=safe, json={'test': True})
        finally:
            await db.aclose()

    with pytest.raises(OCError) as caught:
        if asynchronous:
            asyncio.run(run())
        else:
            with db:
                db._request(method, '/health', retry_safe=safe, json={'test': True})
    assert caught.value.status == status
    assert len(calls) == attempts
    assert [r.headers['x-oc-attempt'] for r in calls] == [str(i+1) for i in range(attempts)]
    assert len({r.headers['x-oc-logical-request-id'] for r in calls}) == 1
    if method in {'POST', 'PUT', 'DELETE'}:
        assert len({r.headers['idempotency-key'] for r in calls}) == 1


@pytest.mark.parametrize('cls', [OriginChain, AsyncOriginChain])
def test_zero_retry_after_survives_error_mapping(cls):
    with pytest.raises(OCRateLimitedError) as caught:
        cls._raise_for(httpx.Response(429, json={}, headers={'Retry-After': '0'}))
    assert caught.value.retry_after == 0


@pytest.mark.parametrize('asynchronous', [False, True])
def test_public_sql_parameters_and_explicit_retries(asynchronous):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503 if len(calls) == 1 else 200,
                              json={'kind': 'select', 'rows': [{'n': 7}]},
                              headers={'Retry-After': '0'})

    cls = AsyncOriginChain if asynchronous else OriginChain
    db = cls.__new__(cls)
    db.max_retries, db.max_retry_time, db._diagnostics, db.tenant = 1, 30, None, 'test'
    client = httpx.AsyncClient if asynchronous else httpx.Client
    db._client = client(base_url='http://test.invalid', transport=httpx.MockTransport(handler))
    if not asynchronous:
        db.sql = _SqlNamespace(db)

    async def run():
        async with db:
            return await db.sql_one('SELECT $1', [7], retry_safe=True, idempotency_key='caller-key')

    if asynchronous:
        result = asyncio.run(run())
    else:
        with db:
            result = db.sql_one('SELECT $1', [7], retry_safe=True, idempotency_key='caller-key')
    assert result == {'n': 7}
    assert len(calls) == 2
    assert all(json.loads(r.content) == {'sql': 'SELECT $1', 'params': [7]} for r in calls)
    assert all(r.headers['idempotency-key'] == 'caller-key' for r in calls)


def test_async_cancellation_during_backoff_stops_retries():
    async def run():
        reached = asyncio.Event()
        calls = []

        def handler(request):
            calls.append(request)
            reached.set()
            return httpx.Response(503, headers={'Retry-After': '20'})

        db = AsyncOriginChain.__new__(AsyncOriginChain)
        db.max_retries, db.max_retry_time, db._diagnostics = 3, 30, None
        db._client = httpx.AsyncClient(base_url='http://test.invalid', transport=httpx.MockTransport(handler))
        async with db:
            task = asyncio.create_task(db._request('GET', '/health'))
            await reached.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert len(calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_network_failure_retries_only_safe_methods(asynchronous, method):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadError('connection reset', request=request)
        return httpx.Response(200, json={'ok': True})

    cls = AsyncOriginChain if asynchronous else OriginChain
    db = cls.__new__(cls)
    db.max_retries, db.max_retry_time, db._diagnostics = 2, 30, None
    db._backoff = lambda attempt: 0
    client = httpx.AsyncClient if asynchronous else httpx.Client
    db._client = client(base_url='http://test.invalid', transport=httpx.MockTransport(handler))

    async def run():
        async with db:
            return await db._request(method, '/health')

    def call():
        if asynchronous:
            return asyncio.run(run())
        with db:
            return db._request(method, '/health')

    if method == 'GET':
        assert call().json() == {'ok': True}
        assert len(calls) == 2
    else:
        with pytest.raises(OCError, match='transport error'):
            call()
        assert len(calls) == 1


@pytest.mark.parametrize('asynchronous', [False, True])
def test_retry_window_includes_time_spent_in_attempt(asynchronous):
    import time
    calls = []

    def handler(request):
        calls.append(request)
        time.sleep(0.02)
        return httpx.Response(503, headers={'Retry-After': '0'})

    cls = AsyncOriginChain if asynchronous else OriginChain
    db = cls.__new__(cls)
    db.max_retries, db.max_retry_time, db._diagnostics = 3, 0.005, None
    client = httpx.AsyncClient if asynchronous else httpx.Client
    db._client = client(base_url='http://test.invalid', transport=httpx.MockTransport(handler))

    async def run():
        async with db:
            await db._request('GET', '/health')

    with pytest.raises(OCError):
        if asynchronous:
            asyncio.run(run())
        else:
            with db:
                db._request('GET', '/health')
    assert len(calls) == 1
