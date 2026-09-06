# Commands contract from AGENTS.md — every target wraps uv;
# agents never invoke pip/pytest/mypy directly.
.PHONY: install test lint type check site-build

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

# v7 (T2.30): render the Pi-local record (data/project/docs) to the static site with
# the arm64 mkdocs-material image — on demand; the host crontab line (setup.sh step
# 9/9, every 15 min UTC) does the scheduled rebuild. Dumb and LLM-free (rule-11 spirit).
site-build:
	docker run --rm --user "$$(id -u):$$(id -g)" -v "$(CURDIR):/workspace" squidfunk/mkdocs-material build -f /workspace/mkdocs.yml -d /workspace/data/site
