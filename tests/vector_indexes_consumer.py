from originchain import (
    BatchWriteError,
    OCAuthError,
    OCError,
    OCNotFoundError,
    OCRecordApiNotEnabledError,
    OCServerError,
    OriginChain,
)

REQUEST_ID = "84f92822-5837-432e-981a-7b24ad65e16c"

with OriginChain.from_env() as client:
    vi = client.vector_indexes
    assert vi.create_index("demo", dimension=2, metric="cosine")["future_field"]["kept"]
    assert vi.describe_index("demo/one")["name"] == "demo"
    assert [ix["name"] for ix in vi.iter_indexes(limit=1)] == ["demo", "zeta"]

    out = vi.upsert("demo", "ns", [
        {"id": "one", "values": [1.0, 0.0], "metadata": {"tag": "test", "n": 1}},
        {"id": "two", "sparse_values": {"indices": [3], "values": [0.5]}},
    ])
    assert out["upserted_count"] == 2 and out["namespace_created"] and out["batches"] == 1
    try:
        vi.upsert("demo-partial", "ns", [{"id": f"p{i}"} for i in range(5)], batch_size=2)
    except BatchWriteError as error:
        # p0-p1 acknowledged; p2-p3 failed; p4 never sent.
        assert (error.offset, error.count, error.outcome) == (2, 2, "unknown")
        assert isinstance(error.__cause__, OCError)
        assert error.__cause__.code == "configuration_limit_exceeded"
        assert error.__cause__.reason == "exact_index_capacity"
    else:
        raise AssertionError("Expected failure: partial upsert")

    res = vi.search("demo", "ns", k=2, vector=[1.0, 0.0], quality="exact",
                    filter_expr={"tag": {"$eq": "test"}}, include_metadata=True)
    assert [hit["id"] for hit in res["hits"]] == ["one", "two"]
    assert res["hits"][0]["metadata"] == {"tag": "test", "n": 1} and res["future_field"]
    hybrid = vi.search("demo", "ns", k=2, vector=[1.0, 0.0],
                       lexical={"field": "body", "query": "hello"})
    assert hybrid["mode"] == "hybrid"
    got = vi.fetch("demo", "ns", ["one", "missing"], include_values=True)
    assert got["records"]["one"]["values"] == [1.0, 0.0] and got["missing"] == ["missing"]
    assert vi.delete("demo", "ns", ["one"])["deleted"] == 1
    job = vi.delete_by_filter("demo", "ns", {"tag": {"$in": ["a", "b"]}})
    assert vi.get_job("demo", job["job_id"])["state"] == "succeeded"
    assert vi.list_namespaces("demo")["namespaces"][0]["name"] == "ns"
    assert vi.index_stats("demo")["total_record_count"] == 1
    assert vi.count("demo", "ns", filter={"tag": "test"})["count"] == 1

    failures = [
        (lambda: vi.describe_index("contract-unauthorized"), OCAuthError, 401, "unauthorized"),
        (lambda: vi.describe_index("contract-key-scope"), OCAuthError, 403, "key_scope_insufficient"),
        (lambda: vi.describe_index("contract-missing"), OCNotFoundError, 404, "index_not_found"),
        (lambda: vi.search("demo", "ns", k=1, sparse={"indices": [3], "values": [0.5]}),
         OCError, 409, "index_capability_missing"),
        (lambda: vi.describe_index("contract-not-enabled"), OCRecordApiNotEnabledError, 501,
         "not_implemented"),
        (lambda: vi.search("demo", "ns", k=3, vector=[1.0, 0.0]), OCServerError, 503,
         "index_rebuilding"),
        (lambda: vi.describe_index("contract-redirect"), OCError, 307, None),
    ]
    for call, kind, status, code in failures:
        try:
            call()
        except OCError as error:
            assert isinstance(error, kind), (type(error), kind)
            assert error.status == status and error.code == code, (error.status, error.code)
            assert error.request_id == REQUEST_ID
            if code is not None:
                assert str(error) == "server detail"
        else:
            raise AssertionError(f"Expected failure: {status}")
print("Python vector index consumer assertions passed")
