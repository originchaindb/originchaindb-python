"""Publication gates are tested without creating tags or contacting registries."""
import importlib.util
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_gate", ROOT / "scripts/release_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def invoke(tmp_path, monkeypatch, version="0.11.0rc1", **overrides):
    # package.json is also supported by the shared gate and keeps these controls
    # independent of tomllib, which the Python 3.12 release runner supplies.
    (tmp_path / "package.json").write_text('{"version":"' + version + '"}')
    monkeypatch.chdir(tmp_path)
    env = {"RELEASE_VERSION": version, "GITHUB_REF": "refs/heads/main",
           "GITHUB_REPOSITORY": "originchaindb/originchaindb-python",
           "GITHUB_ACTOR": "Originchain-ops", "GITHUB_TRIGGERING_ACTOR": "Originchain-ops",
           "GITHUB_OUTPUT": str(tmp_path / "output")}
    env.update(overrides)
    with patch.dict(os.environ, env, clear=True), patch.object(subprocess, "check_output", return_value=""):
        gate.main()
    return (tmp_path / "output").read_text()


@pytest.mark.parametrize("version,prerelease", [("0.11.0", "false"), ("0.11.0rc1", "true")])
def test_stable_and_candidate_versions(tmp_path, monkeypatch, version, prerelease):
    assert "prerelease=" + prerelease in invoke(tmp_path, monkeypatch, version)


@pytest.mark.parametrize("version", ["0.11.0.dev0", "0.11.0+local", "0.11.0.post1", "v0.11.0", "00.11.0", "0.11.0-rc.1", "0.11.0rc01", "0.11.0\n"])
def test_non_release_versions_fail(tmp_path, monkeypatch, version):
    with pytest.raises(SystemExit):
        invoke(tmp_path, monkeypatch, version)


@pytest.mark.parametrize("setting,value", [
    ("GITHUB_REPOSITORY", "example/python"), ("GITHUB_ACTOR", "github-actions[bot]"),
    ("GITHUB_TRIGGERING_ACTOR", "different-operator"), ("GITHUB_REF", "refs/heads/feature"),
    ("RELEASE_VERSION", "0.12.0"),
])
def test_identity_ref_and_metadata_must_match(tmp_path, monkeypatch, setting, value):
    with pytest.raises(SystemExit):
        invoke(tmp_path, monkeypatch, **{setting: value})


def test_existing_tags_fail(tmp_path, monkeypatch):
    with patch.dict(os.environ, {"RELEASE_VERSION": "0.11.0rc1", "GITHUB_REF": "refs/heads/main",
                               "GITHUB_REPOSITORY": "originchaindb/originchaindb-python",
                               "GITHUB_ACTOR": "Originchain-ops", "GITHUB_TRIGGERING_ACTOR": "Originchain-ops"}, clear=True):
        (tmp_path / "package.json").write_text('{"version":"0.11.0rc1"}')
        monkeypatch.chdir(tmp_path)
        with patch.object(subprocess, "check_output", return_value="v0.11.0rc1\n"), pytest.raises(SystemExit, match="already exists"):
            gate.main()
