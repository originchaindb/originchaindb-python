# Using the client

Create `db` as shown in the [quickstart](../README.md). These independent examples
assume an existing `demo.items` schema and an appropriate search index.

```python
result = db.sql.run("SELECT $1 AS value", [7])
row = db.rows.get("demo.items", "one")
vectors = db.vector.search("demo.items", [1.0, 0.0])
text = db.fts.search("demo.items", "body", "hello")
graph = db.graph.query("MATCH (n) RETURN $name", {"name": "one"})
```

## Results, errors, and lifetime

`SqlExecResult.rows` contains dictionaries. `rows_affected` is an integer or
`None`; `raw` preserves the envelope. Vector hits expose `id`, `score`, `metadata`
and `raw`. Full-text results expose `hits`, `facets`, and `raw`.

Catch `originchain.OCError`; inspect `status` and `request_id` for HTTP failures.
`OCBatchError.result` contains the partial SQL result. `OCProtocolError` identifies
malformed responses.

Use `Client.from_env(timeout=10)` to change the timeout in seconds. Use
`AsyncClient.from_env()` with `async with` and `await` for async applications.
Cancel the application's task to stop waiting locally.

See the [shared behavior contract](contract.md) for parameter binding, batch
semantics, defaults, and retry behavior. The [contract consumer](../tests/consumer.py)
exercises the full common API against a local fixture.
