# Contributing to prowl-bench

Thanks for your interest in contributing. Here's how to get started.

## Setup

```bash
git clone https://github.com/opcastil11/prowl-bench.git
cd prowl-bench
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest tests/ -q
```

Tests run without any API keys -- no LLM calls are made during testing.

## Code Style

We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

Line length is 120. Target is Python 3.10+.

## Pull Requests

1. Fork the repo and create a branch from `main`.
2. Add tests for any new functionality.
3. Make sure `pytest tests/ -q` passes.
4. Make sure `ruff check src/ tests/` passes.
5. Open a PR with a clear description of what changed and why.

## Adding a New Template

Templates live in `src/prowl_bench/templates/`. To add one:

1. Create a new file (e.g., `my_template.py`) that subclasses `BaseBenchmarkTemplate`.
2. Implement `analyze()`, `plan()`, `execute()`, and `interpret()`.
3. Register it in `src/prowl_bench/templates/__init__.py`.
4. Add tests in `tests/`.

## Reporting Issues

Open an issue on GitHub. Include:

- What you ran (`prowl-bench run ...`)
- What you expected
- What happened instead
- Python version and OS

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 license.
