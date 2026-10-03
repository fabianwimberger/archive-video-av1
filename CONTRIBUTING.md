# Contributing

## Quick start

- **Bugs:** Open an issue using the bug-report template.
- **Features:** Open an issue using the feature-request template.
- **PRs:** Fork, branch from `develop`, keep the change focused, open against `develop`.

## Local development

Create a virtual environment and install the backend with its development tools:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]'
make lint
make test
.venv/bin/python -m mypy --config-file backend/pyproject.toml backend/
```

Run `make format` to format Python files.

## Conventions

- Prefix commits semantically (`feat:`, `fix:`, `docs:`, `ci:`, `deps:`).
- One logical change per PR.
- Make sure CI is green before requesting review.
- For UI changes, check both a desktop viewport and a phone-sized viewport before opening the PR.

## License

By contributing, you agree that your contributions will be licensed under the project's MIT license.
