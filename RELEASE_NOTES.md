# Python SDK development notes

The source in this repository is `0.11.0.dev0`. It has not been published as a package release.

The supported query families are SQL, full-text search, vector search and graph queries. The synchronous and asynchronous natural-language helpers have been removed. Existing row, schema, transaction and diagnostic operations remain available.

SQL results preserve numeric and temporal strings and expose optional server-supplied `column_types`. Floating special values are decoded only when that metadata identifies a floating type. Stored microsecond precision and special-value support still depend on the deployed engine; the SDK does not enable them.

The repository is maintained at `originchaindb/originchaindb-python`. The distribution and Python import remain `originchain`. See the README for development installation and the changelog for earlier package versions.
