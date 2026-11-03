# Examples

The [catalog program](catalog.py) creates `sdk_examples.products`, writes
two products, and demonstrates SQL, vector search, full-text search, and graph
neighbors. It uses [products.toml](products.toml). Run it only against a development
database after setting `OC_URL` and `OC_TOKEN`:

```sh
python examples/catalog.py
```

See the [main README](../README.md#product-catalog-example) for setup and explanations.

## Read-only query commands

From the repository root, install/build the local SDK:

```sh
python -m pip install .
python examples/query.py --help
```

Set two values: `OC_URL`, your database's address from Connect, and `OC_TOKEN`, an access token from Access tokens. Use `OC_URL` exactly as shown. Use your shell's environment-variable syntax or a secret manager; no credentials belong in these files. Examples use a 30-second timeout and no automatic retries.

| Command | Additional environment | Existing data required |
| --- | --- | --- |
| `python examples/query.py sql` | None | None; binds a string to `SELECT $1 AS message`. |
| `python examples/query.py fts` | `OC_TABLE`, `OC_FTS_FIELD`, `OC_QUERY` | An indexed full-text field containing documents. |
| `python examples/query.py vector` | `OC_TABLE`, `OC_QUERY_VECTOR` | Vectors indexed with cosine distance. Query dimension/model must match. |
| `python examples/query.py graph` | `OC_TABLE`, `OC_RELATION`, `OC_NODE_ID` | A registered relation and source node. |

For example, a two-dimensional toy index can use `OC_QUERY_VECTOR=[1,0]`. It is an illustration of the wire input, not an embedding model. Use real model output for real data. Table names may be `namespace.table`; identifiers are validated before use. The commands only read; they do not create schemas, insert fixtures, or delete user data. Empty search output can simply mean no matches.

Configure data using the [schema](https://originchaindb.com/docs/schemas), [full-text](https://originchaindb.com/docs/fts), [vector](https://originchaindb.com/docs/vector) and [graph](https://originchaindb.com/docs/graph/quickstart) guides. Engine features and permissions still apply.

CI runs these same scripts against a loopback HTTP fixture to check request and response behavior. That check does not certify a live database or new server features. See [Contributing](../CONTRIBUTING.md) for the exact check command.
