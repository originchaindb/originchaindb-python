"""Consumer for the shared fault fixture; not a live token-lifecycle test."""
import os
from dataclasses import dataclass

from originchain import Client, OCCredentialError, OCError, RowDecodeError

url, token = os.environ["OC_URL"], os.environ["OC_TOKEN"]
with Client(url=url, token=token) as client:
    for name, status in [("expired", 401), ("revoked", 401), ("forbidden", 403), ("limited", 429)]:
        try:
            client.sql.run("fault:" + name)
            raise AssertionError("expected HTTP failure")
        except OCError as error:
            assert error.status == status
            assert error.request_id == "84f92822-5837-432e-981a-7b24ad65e16c"
            if name == "limited":
                assert error.retry_after == 2
    try:
        client.sql.run("fault:interrupted")
        raise AssertionError("interrupted write returned success")
    except OCError:
        pass

events = []
with Client(url=url, token=token, timeout=0.15, on_request=events.append) as client:
    try:
        client.sql.run("fault:timeout")
        raise AssertionError("timeout returned success")
    except OCError:
        assert events[-1].error_kind == "timeout"

calls = 0
def provider():
    global calls
    calls += 1
    return f"rotate-{calls}"

events = []
with Client(url=url, token_provider=provider, on_request=events.append) as client:
    try:
        client.sql.run("fault:rotate")
        raise AssertionError("expired provider token succeeded")
    except OCError as error:
        assert error.status == 401 and calls == 1
    result = client.sql.run("fault:rotate")
assert calls == 2
assert [event.error_kind for event in events] == ["authentication", None]
assert all(event.operation == "sql" and event.duration > 0 for event in events)
assert "rotate-" not in repr(events) and url not in repr(events)

@dataclass
class Product:
    id: int
    amount: str

mapped = result.map_rows(lambda row: Product(**row))
assert mapped == [Product(9007199254740993, "1.00000000000000001")]
result.map_rows(lambda row: row.clear())
assert result.rows[0]["id"] == 9007199254740993
try:
    result.map_rows(lambda row: row["missing"])
    raise AssertionError("invalid mapping succeeded")
except RowDecodeError as error:
    assert error.row == 0

with Client(url=url, token_provider=lambda: "bad\nheader") as client:
    try:
        client.sql.run("fault:invalid-provider")
        raise AssertionError("invalid token succeeded")
    except OCCredentialError:
        pass

def broken_observer(event):
    raise RuntimeError("observer failure")

with Client(url=url, token=token, on_request=broken_observer) as client:
    assert len(client.sql.run("fault:observer").rows) == 1
print("Python resilience assertions passed")
