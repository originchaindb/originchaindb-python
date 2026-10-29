# Contributing

Use Python 3.9 or later and an activated virtual environment:

```sh
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -e ".[dev]" build twine
python -m ruff check .
python -m pytest -q tests
python -m build
python -m twine check dist/*
python scripts/verify_package.py
```

`tests/test_examples.py` executes the actual example CLI against a loopback HTTP fixture. It checks SQL, full-text, vector and graph requests without live credentials. Run it alone with `python -m pytest -q tests/test_examples.py`. Optional database tests are separate and require the explicit live-test settings described in the existing workflow.

The package intentionally uses the valid flat layout `originchain/`; imports remain `originchain`. Keep synchronous and asynchronous behavior aligned and add tests for both when changing an operation. The wheel includes the runtime package; the source archive includes contributor material. Test a built wheel rather than relying only on imports from your checkout.

For a pull request, explain the user-visible change and list the commands run. Add a regression for a bug and update the README/examples for API changes. Keep credentials and database contents out of examples and reports. Preserve dated verification in `validation/` and immutable release evidence. Source changes do not authorize publication.
