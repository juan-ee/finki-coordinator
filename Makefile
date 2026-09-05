# Commands contract from AGENTS.md — every target wraps uv;
# agents never invoke pip/pytest/mypy directly.
.PHONY: install test lint type check sync

install:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff format --check
	uv run ruff check

type:
	uv run mypy --strict src/coordinator

check: lint type test

# Deterministic Drive -> knowledge-cache sync (v6.1, T2.18): no LLM in the data path.
sync:
	uv run python scripts/sync_knowledge.py
