"""Validate a release request before any publish job can run."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

def main():
    expected = os.environ.get("RELEASE_VERSION", "")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:rc(?:0|[1-9][0-9]*))?", expected):
        sys.exit("RELEASE_VERSION must be canonical X.Y.Z or X.Y.ZrcN (no development, local or post release)")
    if os.environ.get("GITHUB_REPOSITORY") != "originchaindb/originchaindb-python":
        sys.exit("Releases must use the maintained originchaindb Python repository")
    if os.environ.get("GITHUB_ACTOR") != "Originchain-ops" or os.environ.get("GITHUB_TRIGGERING_ACTOR", "Originchain-ops") != "Originchain-ops":
        sys.exit("Only Originchain-ops may request or rerun a release")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        sys.exit("Releases must run from main")
    if Path("package.json").exists():
        actual = json.loads(Path("package.json").read_text())["version"]
    elif Path("pyproject.toml").exists():
        import tomllib
        actual = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    else:
        match = re.search(r'const Version = "([^"]+)"', Path("doc.go").read_text())
        if not match:
            sys.exit("Go SDK version is missing")
        actual = match.group(1)
    if actual != expected:
        sys.exit("Requested version does not match SDK metadata")
    if subprocess.check_output(["git", "tag", "--list", "v" + expected], text=True).strip():
        sys.exit("Release tag already exists; inspect the existing release before retrying")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write("version=" + expected + "\n")
        output.write("prerelease=" + ("true" if "rc" in expected else "false") + "\n")
    print("Release metadata verified for " + expected)

if __name__ == "__main__":
    main()
