"""Guard: this repository teaches only OC_URL + OC_TOKEN.

The lists are the connect contract's ``never_access`` and ``wire_terms``.
Matching is exact and case-sensitive on the raw bytes, the same as
``grep -a -F`` without ``-i``.

Every file in the repository is checked, found by walking the tree, so a new
folder or file is covered without being listed here. Only local tool output
(``.git``, virtual environments, caches, build output) is skipped.

A file may contain a banned term only if ``ALLOWED`` names that exact term for
that exact file, with the reason: the two shims that keep deprecated names
working, the tests that prove those names still work or are not read, and this
guard's own list. Anything else found in an allowed file is still flagged.

``test_guard_runs_the_real_walk_on_a_fixture_tree`` runs the same walk and the
same allowance code on a built tree: the files it must flag are the positive
controls, the allowed and skipped ones are the negative controls.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]

# TODO(connect contract): read these lists from the published connect contract
# (connect.v1.json) once it is available.
NEVER_ACCESS = [
    "ORIGINCHAIN_HOST", "ORIGINCHAIN_PORT", "ORIGINCHAIN_USER", "ORIGINCHAIN_PASSWORD",
    "ORIGINCHAIN_DB", "ORIGINCHAIN_TOKEN", "ORIGINCHAIN_URL", "ORIGINCHAIN_TENANT",
    "ORIGINCHAIN_DB_USER", "ORIGINCHAIN_CA", "OC_HOST", "OC_TENANT", "OC_BASE_URL",
    "OC_BEARER", "OC_API_KEY", "OC_ENDPOINT", "OC_DB_USER", "OC_DB_KEY", "OC_DB_PASSWORD",
    "OC_ES_URL", "X-OC-DB-User", "x-oc-db-user", "X-OC-DB-Key", "x-oc-db-key", "X-OC-Tenant",
    "/v1/tenants/$", "/v1/tenants/{tenant}", "/v1/tenants/YOUR_TENANT", "oc_live_",
]
WIRE_TERMS = ["psycopg", "node-postgres", "postgresql://", "PGPASSWORD", ":5432", "sslmode"]
BANNED = NEVER_ACCESS + WIRE_TERMS

OLD_ENV = {"OC_BASE_URL", "OC_TENANT", "OC_BEARER"}
IDENTITY_HEADERS = {"X-OC-DB-User", "X-OC-DB-Key"}

# file -> (the exact banned terms it may contain, why)
ALLOWED: dict[str, tuple[frozenset[str], str]] = {
    "originchain/_connect.py": (
        frozenset(OLD_ENV), "reads the old environment names for one release"),
    "originchain/_identity.py": (
        frozenset(IDENTITY_HEADERS),
        "sends the database-user headers, needed where access tokens are not supported yet"),
    "tests/test_access_names.py": (frozenset(BANNED), "this guard's own lists"),
    "tests/test_connect.py": (
        frozenset(OLD_ENV | {"OC_DB_USER", "OC_DB_KEY", "X-OC-DB-User"}),
        "proves the old names still work, warn, and are never read when the new ones are set"),
    "tests/test_examples.py": (
        frozenset(OLD_ENV), "proves the example still runs on the old names, with warnings"),
    "tests/test_identity.py": (
        frozenset(IDENTITY_HEADERS), "proves the database-user headers are sent unchanged"),
    "tests/test_async_parity.py": (
        frozenset({"x-oc-db-user", "x-oc-db-key"}),
        "proves the async client sends the same database-user headers"),
}

# Directory names that hold local tool output, never repository content.
SKIPPED_DIRS = {
    ".git", ".venv", "venv", "env", ".tox", ".nox", ".mypy_cache", ".ruff_cache",
    ".pytest_cache", "__pycache__", "build", "dist", "node_modules", ".idea", ".vscode",
}


def _skipped(name: str) -> bool:
    return name in SKIPPED_DIRS or name.endswith(".egg-info")


def guarded_files(root: Path) -> list[str]:
    """Every file under ``root`` except local tool output, as posix paths."""
    found: list[str] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not _skipped(d))
        for name in files:
            found.append((Path(folder) / name).relative_to(root).as_posix())
    return sorted(found)


def banned_terms_in(data: bytes) -> list[str]:
    return [term for term in BANNED if term.encode() in data]


def violations(
    root: Path, allowed: Mapping[str, tuple[frozenset[str], str]] = ALLOWED
) -> dict[str, list[str]]:
    """``{file: [banned terms it may not contain]}`` for ``root``."""
    out: dict[str, list[str]] = {}
    for name in guarded_files(root):
        permitted = allowed.get(name, (frozenset(), ""))[0]
        bad = [t for t in banned_terms_in((root / name).read_bytes()) if t not in permitted]
        if bad:
            out[name] = bad
    return out


def test_repository_names_only_the_one_access_path():
    names = guarded_files(ROOT)
    # Positive control on the walk itself: it reaches docs, package metadata,
    # the package, examples, scripts, workflows and tests.
    assert {
        "README.md", "CHANGELOG.md", "pyproject.toml", "originchain/client.py",
        "examples/query.py", "scripts/verify_package.py", ".github/workflows/ci.yml",
        ".github/workflows/e2e.yml", "tests/e2e_smoke.py", "tests/test_connect.py",
    } <= set(names)
    readme = (ROOT / "README.md").read_bytes()
    assert b"OC_URL" in readme and b"OC_TOKEN" in readme
    assert violations(ROOT) == {}


def test_every_allowance_is_still_needed():
    """An allowance for a term the file no longer contains is stale and would
    silently let that term back in later."""
    for name, (terms, _why) in ALLOWED.items():
        assert name in guarded_files(ROOT), name
        assert terms <= set(banned_terms_in((ROOT / name).read_bytes())), name


def test_guard_flags_every_banned_term():
    fixture = "\n".join(f"export {term}=x" for term in BANNED).encode()
    assert banned_terms_in(fixture) == BANNED
    for term in BANNED:
        assert banned_terms_in(f"see {term} here".encode()) != [], term


def test_guard_is_case_sensitive_like_grep_F():
    assert banned_terms_in(b"oc_base_url") == []
    assert banned_terms_in(b"OC_BASE_URL") == ["OC_BASE_URL"]


def test_guard_runs_the_real_walk_on_a_fixture_tree(tmp_path):
    files = {
        # Must be flagged (positive controls), one per kind of place.
        "README.md": "Use OC_URL and OC_TOKEN. Or postgresql://u@h:5432/db",
        "pyproject.toml": 'description = "set OC_HOST"',
        "scripts/live.py": "import psycopg",
        "docs/new/guide.md": "curl https://h/v1/tenants/$ID/sql",
        "originchain/sub/deep.py": "ORIGINCHAIN_HOST = 1",
        "examples/nested/app.py": "os.environ['OC_TENANT']",
        ".github/workflows/live.yml": "OC_DB_KEY: x",
        "tests/e2e_smoke.py": "os.environ['OC_BEARER']",
        "tests/test_new.py": "sslmode=require",
        # An allowed file is flagged for any term outside its own allowance.
        "originchain/_connect.py": "OC_BASE_URL OC_TENANT OC_BEARER psycopg",
        # Must pass (negative controls).
        "originchain/_identity.py": "X-OC-DB-User X-OC-DB-Key",
        "tests/test_connect.py": "OC_BASE_URL OC_DB_USER",
        "CHANGELOG.md": "oc_base_url is not the banned name",
        ".venv/lib/site.py": "import psycopg",
        "build/lib/x.py": "OC_TENANT",
        "pkg.egg-info/PKG-INFO": "OC_TENANT",
        "originchain/__pycache__/x.pyc": "OC_TENANT",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    assert violations(tmp_path) == {
        "README.md": ["postgresql://", ":5432"],
        "pyproject.toml": ["OC_HOST"],
        "scripts/live.py": ["psycopg"],
        "docs/new/guide.md": ["/v1/tenants/$"],
        "originchain/sub/deep.py": ["ORIGINCHAIN_HOST"],
        "examples/nested/app.py": ["OC_TENANT"],
        ".github/workflows/live.yml": ["OC_DB_KEY"],
        "tests/e2e_smoke.py": ["OC_BEARER"],
        "tests/test_new.py": ["sslmode"],
        "originchain/_connect.py": ["psycopg"],
    }
    # Without its allowance, an allowed file is flagged too: the allowance,
    # not a failed read, is what lets it pass.
    assert violations(tmp_path, allowed={})["originchain/_identity.py"] == [
        "X-OC-DB-User", "X-OC-DB-Key"]
