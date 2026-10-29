# Python SDK development notes

The source in this repository is `0.11.0rc1`. It has not been published as a package release.

The supported query families are SQL, full-text search, vector search and graph queries. The synchronous and asynchronous natural-language helpers have been removed. Existing row, schema, transaction and diagnostic operations remain available.

SQL results preserve numeric and temporal strings and expose optional server-supplied `column_types`. Floating special values are decoded only when that metadata identifies a floating type. Stored microsecond precision and special-value support still depend on the deployed engine; the SDK does not enable them.

The repository is maintained at `originchaindb/originchaindb-python`. The distribution and Python import remain `originchain`. See the README for development installation and the changelog for earlier package versions.

Release preparation requires the exact candidate wheel to pass fresh-consumer SQL, FTS, vector and graph acceptance against the supported test database. Missing live configuration blocks release.

Before authorized publication, verify the PyPI project owner and its trusted publisher for `originchaindb/originchaindb-python`, workflow `release.yml`, environment `production-pypi`. A secret name alone does not establish publishing permission. After upload, install `originchain==0.11.0rc1` in a new virtual environment and verify the registry wheel hash against the accepted wheel. An authenticated `Originchain-ops` maintainer then records the GitHub prerelease and tag from those same bytes. No release or package is published by this preparation change.
