# Releasing the Python SDK

Package publication is currently on hold. Source updates and CI builds do not create a package release.

When publication resumes, configure a PyPI trusted publisher for `originchaindb/originchaindb-python`, workflow `release.yml`, environment `production-pypi`. Repository recreation does not migrate publisher settings, environment protections, or secrets. Configure approved nonproduction credentials before running the live workflow; never add credentials to source or logs.

The Release workflow accepts a stable `X.Y.Z` version already committed on `main`. Its version gate rejects development versions and existing tags. It runs the compatibility matrix, live database checks, and package validation before publishing the exact tested wheel and source archive. It creates the tag and GitHub Release only after those checks and the package upload succeed.

After an authorized release, verify the public package metadata and install the published version in a fresh environment. Keep the distribution and import name `originchain` unless a separate package migration is planned.
