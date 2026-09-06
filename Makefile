# Commands contract from AGENTS.md — every target wraps uv;
# agents never invoke pip/pytest/mypy directly.
.PHONY: install test lint type check

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
