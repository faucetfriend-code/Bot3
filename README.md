# Bot3

Multi-strategy perpetual-futures trading bot (Pacifica testnet, Blofin demo).
`CLAUDE.md` is the project handbook: architecture, signal flow, gotchas,
environment variables and how to run the bot. `AGENTS.md` has the code style
rules. This file only documents the CI pipeline.

## Continuous integration

`.github/workflows/ci.yml` runs on every push to `main` and on every pull
request against `main`. Python 3.11 throughout. Jobs run in parallel.

| Job | What it runs | Blocking |
|-----|--------------|----------|
| Lint & Format | `ruff check` and `ruff format --check` on `trading_bot_v2/` | yes |
| Tests | `pytest trading_bot_v2/tests/` with coverage | yes |
| Type Check | `mypy trading_bot_v2/ --ignore-missing-imports` | no, until the strict-clean work lands |
| Security Scan | `bandit -c pyproject.toml -r trading_bot_v2/` | no (advisory) |
| Docker Build | builds the `Dockerfile` without pushing | yes |

The Tests job prints a per-file coverage table in the run's job summary and
uploads `coverage.xml`, the HTML report and a JUnit file as the
`coverage-report` artifact (kept 14 days). The Security Scan job uploads
`bandit-report.json` as `security-report`.

### Formatting gate: ruff format, not Black

The gate is `ruff format --check` at line length 88. `[tool.black]` in
`pyproject.toml` carries the same settings so a local `black` run lands
close, but Black is not run in CI: Black's stable style and ruff format still
differ on a few constructs (ruff hugs multi-line strings inside call
parentheses, Black wraps multi-line ternaries in parentheses), and the tree is
formatted with ruff. Two gates would fight over the same files. If you format
with Black, run `ruff format trading_bot_v2/` afterwards.

### Pinned tool versions

Every tool CI runs is pinned once, under `[project.optional-dependencies]` in
`pyproject.toml`, in four groups: `lint`, `typing`, `test`, `security`. Each
job installs only its own group. Bump a pin there and CI follows; nothing is
pinned in the workflow file itself.

### Running the same checks locally

```bash
python -m pip install -r trading_bot_v2/requirements.txt
python -m pip install -e ".[dev]"

ruff check trading_bot_v2/
ruff format --check trading_bot_v2/
mypy trading_bot_v2/ --ignore-missing-imports
pytest trading_bot_v2/tests/ --cov --cov-report=term-missing:skip-covered
bandit -c pyproject.toml -r trading_bot_v2/ -q
```

A bare `pytest` from the repo root also works: `pyproject.toml` sets
`pythonpath = ["."]` and `testpaths = ["trading_bot_v2/tests"]`, so the suite
imports without an editable install. Timing-sensitive tests are excluded by
default (`-m 'not perf'`); run them deliberately with `pytest -m perf`.

### Workflow hygiene

- `permissions: contents: read` is the only grant; no job writes to GitHub.
- A new push to a pull request cancels the run it supersedes. Pushes to
  `main` always run to completion.
- pip downloads are cached per job, keyed on `pyproject.toml` (plus
  `trading_bot_v2/requirements.txt` for the Tests job).
- No system packages are installed: every runtime dependency ships a Linux
  wheel for CPython 3.11, and the Python `playwright` package installs without
  a browser.
