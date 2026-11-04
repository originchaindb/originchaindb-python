"""Native consumer for the shared bounded iteration contract."""
import asyncio

from originchain import BatchWriteError, Client, OCProtocolError


with Client.from_env() as db:
    def source(consumed):
        reusable = {}
        for key in "abcde":
            consumed.append(key)
            reusable["id"] = key
            yield reusable

    consumed = []
    batches = db.rows.write_batches("bulk.ok", source(consumed), batch_size=2)
    assert consumed == []
    first = next(batches)
    assert consumed == ["a", "b"] and (first.offset, first.count, first.inserted) == (0, 2, 2)
    assert [(b.offset, b.count, b.inserted) for b in batches] == [(2, 2, 2), (4, 1, 1)]

    for name, offset, count in [("fail", 2, 4), ("lost", 0, 2), ("unknown", 0, 2)]:
        consumed = []
        try:
            list(db.rows.write_batches("bulk." + name, source(consumed), batch_size=2))
            raise AssertionError("failed write returned success")
        except BatchWriteError as error:
            assert error.offset == offset and error.count == 2 and error.outcome == "unknown"
            assert error.__cause__ is not None and len(consumed) == count

    reports = list(db.rows.write_batches("bulk.bytes", ({"id": k, "value": "x" * 20} for k in "abc"), max_bytes=64))
    assert [b.count for b in reports] == [1, 1, 1]
    consumed = []
    batches = db.rows.write_batches("bulk.stop", source(consumed), batch_size=2)
    next(batches)
    batches.close()
    assert len(consumed) == 2
    assert list(db.rows.write_batches("bulk.never", [])) == []
    try:
        next(db.rows.write_batches("bulk.never", [{"id": "too-large"}], max_bytes=4))
        raise AssertionError("oversized row accepted")
    except BatchWriteError as error:
        assert error.offset == 0 and error.outcome == "not_sent"
    try:
        next(db.rows.write_batches("bulk.never", [], batch_size=0))
        raise AssertionError("invalid size accepted")
    except ValueError:
        pass
    try:
        next(db.rows.write_batches("bulk.never", source([]), cancelled=lambda: True))
        raise AssertionError("canceled write ran")
    except asyncio.CancelledError:
        pass

    assert [r["id"] for r in db.rows.scan("scan.items", "id", page_size=2)] == list("abcde")
    stream = db.rows.scan("scan.stop", "id", page_size=2)
    assert next(stream)["id"] == "a"
    stream.close()
    pages = list(db.rows.pages("scan.resume", "id", page_size=2, after="b"))
    assert [p.next_cursor for p in pages] == ["d", None]
    assert [r["id"] for p in pages for r in p.rows] == list("cde")
    for name in ["bad", "numeric", "oversize"]:
        try:
            next(db.rows.pages("scan." + name, "id", page_size=2))
            raise AssertionError("invalid page accepted")
        except OCProtocolError:
            pass
    assert list(db.rows.scan("scan.empty", "id", page_size=2)) == []
    try:
        next(db.rows.scan("scan.items;DELETE", "id"))
        raise AssertionError("invalid identifier accepted")
    except ValueError:
        pass
print("Python iteration assertions passed")
