"""Validate a release request before any publish job can run."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

def main():
    expected = os.environ.get("RELEASE_VERSION", "")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", expected):
        sys.exit("RELEASE_VERSION must be a stable X.Y.Z version")
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
    print("Release metadata verified for " + expected)

if __name__ == "__main__":
    main()
