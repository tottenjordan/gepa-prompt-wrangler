# Thin wrappers over the commands CI runs, so `make check` locally and a green
# build mean the same thing. Tabs are required by make.
.PHONY: dev format lint test check hooks audit

dev:
	uv sync --all-groups

format:
	uv run ruff format .

lint:
	uv run ruff format --check .
	uv run ruff check .
	uv run ty check wrangler/

test:
	uv run pytest tests/ -v

audit:
	uvx pip-audit --strict

hooks:
	uvx prek install

check: lint test
