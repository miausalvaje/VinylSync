# Contributing

## Development setup

1. Install Python 3.11 or newer.
2. Create and activate a virtual environment.
3. Install development dependencies:

   ```powershell
   python -m pip install -e ".[dev]"
   ```

4. Install the pre-commit hooks:

   ```powershell
   pre-commit install
   ```

## Tests and quality checks

```powershell
pytest
ruff check .
ruff format --check .
mypy
```

Run `ruff format .` to apply formatting.

## Code style

- Use clear, typed Python code compatible with Python 3.11.
- Keep public documentation, comments, errors, and UI text in English.
- Add focused tests for behavior changes.
- Avoid committing credentials, local databases, generated files, or machine-specific paths.

## Commit convention

Use [Conventional Commits](https://www.conventionalcommits.org/), for example:

- `feat: add LRCLIB fallback search`
- `fix: correct lyric offset calculation`
- `docs: update Windows setup instructions`

## Issues and pull requests

Search existing issues before opening a new one. Include reproduction steps,
expected behavior, actual behavior, and relevant logs with secrets removed.

Pull requests should explain the motivation, summarize the implementation, and
include tests for changed behavior. Complete the pull request checklist before
requesting review.
