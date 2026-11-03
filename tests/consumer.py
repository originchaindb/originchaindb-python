from originchain import OriginChain, OCBatchError, OCError, OCProtocolError

with OriginChain.from_env() as client:
    result = client.sql.run("contract:select", [7, "quote' / 雪", None])
    assert result.rows[0]["id"] == 9007199254740993
    assert result.rows[0]["amount"] == "1.00000000000000001" and result.raw["future_field"]["kept"]
    result = client.sql.run("contract:update")
    assert (
        result.rows_affected == 2
        and result.rows_buffered == 3
        and result.columns == ["id"]
        and len(result.rows) == 1
    )
    result = client.sql.run("contract:unknown")
    assert result.kind == "future_statement" and result.rows_affected is None
    for name in ["batch", "unavailable", "unauthorized", "redirect", "malformed", "invalid-rows"]:
        try:
            client.sql.run("contract:" + name)
        except (OCError, ValueError) as error:
            if name in {"malformed", "invalid-rows"}:
                assert isinstance(error, OCProtocolError)
            if name == "batch":
                assert (
                    isinstance(error, OCBatchError) and error.result.results[0].rows_affected == 1
                )
            if name == "unauthorized":
                assert (
                    error.status == 401
                    and error.request_id == "84f92822-5837-432e-981a-7b24ad65e16c"
                )
            if name == "unavailable":
                assert error.status == 503
        else:
            raise AssertionError("Expected failure: " + name)
    client.rows.get("demo/items", "a b?/%")
    client.rows.put("demo.items", {"id": "one", "nullable": None})
    client.rows.put_batch("demo.items", [{"id": "one"}, {"id": "two"}])
    client.schemas.list()
    client.schemas.register('[schema]\nname="demo.items"\n')
    client.vector.put("demo.items", "one", [1.0, 0.0], {"tag": "test"})
    assert [hit.id for hit in client.vector.search("demo.items", [1.0, 0.0])] == ["one", "two"]
    client.vector.delete("demo.items", "one")
    client.fts.index("demo.items", "body", "one", "hello world")
    assert client.fts.search("demo.items", "body", "hello").hits[0].doc_id == "one"
    phrase = client.fts.search("demo.items", "body", "phrase", mode="phrase")
    assert phrase.hits[0].doc_id == "two" and phrase.hits[0].score == 0
    enriched = client.fts.search("demo.items", "body", "highlight", highlight=True, facets=["tag"])
    assert enriched.facets and enriched.hits[0].highlights
    client.graph.query("MATCH (n) RETURN $name", {"name": "one"})
    client.graph.neighbors("demo.items", pk="one", rel="related")
    client.graph.bfs("demo.items", pk="one", rel="related", max_depth=5)
print("Python consumer assertions passed")
