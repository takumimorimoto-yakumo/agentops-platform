# Contributing to agentops-platform

Thank you for your interest in contributing. This document covers the development environment, testing, and the conventions we follow.

## Development environment

### Requirements

- Python 3.11+
- Git

### Setup

```bash
git clone https://github.com/takumimorimoto-yakumo/agentops-platform.git
cd agentops-platform
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` for local configuration:

```bash
cp .env.example .env
```

## Running tests

```bash
pytest
```

All 100 tests must pass. The test suite is fully self-contained (no network calls, no LLM, no GitHub).

For a specific file:

```bash
pytest tests/test_rollback.py -v
```

## Running the autonomy demo

The demo shows the end-to-end killer scenario with no external services:

```bash
python -m agentops_platform.examples.autonomy_demo
```

With the live dashboard:

```bash
uvicorn agentops_platform.main:app --port 8080 &
python -m agentops_platform.examples.autonomy_demo --with-server
# Open: http://localhost:8080/dashboard
```

## Code conventions

- **Language**: all code, comments, docstrings, and documentation are in English.
- **Style**: enforced by [Ruff](https://github.com/astral-sh/ruff) (`line-length = 100`, `target-version = py311`).
- **Types**: all public functions and methods must be type-annotated. Mypy strict mode is configured.
- **No hard-coding**: thresholds, model IDs, canary steps, and all domain constants must be imported from `config/defaults.py`. Never inline magic numbers.
- **No new LLM dependencies**: the only permitted LLM provider is Google Gemini via `google-genai` (the unified SDK covering both AI Studio and the Gemini Enterprise Agent Platform). Do not add OpenAI, Anthropic, Cohere, Ollama, or any other LLM dependency.

### Code quality checks

```bash
# Linting
.venv/bin/ruff check src/ tests/

# Type checking
.venv/bin/mypy src/
```

## Branch and commit conventions

- **Working branch**: `develop`. Open PRs against `develop`; `main` is the release branch.
- **Commit messages**: English, imperative mood, conventional format preferred (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
- **Atomic commits**: one logical change per commit. Do not mix refactors with feature changes.

## Pull request process

1. Fork the repository and create a branch off `develop`.
2. Make your changes, add or update tests, and ensure all tests pass.
3. Run the autonomy demo to confirm the end-to-end scenario still works.
4. Open a PR against `develop` with a clear description of what changed and why.
5. All CI checks (tests, linting) must be green before merge.

## License agreement

By contributing to this project you agree that your contributions will be licensed under the [Apache License 2.0](./LICENSE). Copyright is retained by the original author (Copyright (c) 2026 Takumi Morimoto).

## Project scope

This repository implements the **control plane only**: evaluation, canary state machine, meta-agent decisions, and the audit log. The following are intentionally out of scope:

- Real Cloud Run traffic split integration (requires a deployed service; documented in `deploy/`).
- BigQuery / Cloud Trace adapter implementations (the interface is defined; adapters are private-pluggable).
- Non-ADK agent runtimes (the HTTP contract is framework-agnostic, but only ADK is the reference target).
- General MLOps (model training, dataset management beyond eval suites).
