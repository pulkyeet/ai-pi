.PHONY: check fmt test unit integration db-up db-down db-reset migrate sync

sync:
	uv sync --extra dev

fmt:
	uv run ruff check --fix src tests migrations
	uv run ruff format src tests migrations

check:
	uv run ruff check src tests migrations
	uv run ruff format --check src tests migrations
	uv run mypy
	uv run pytest

test:
	uv run pytest

unit:
	uv run pytest tests/unit

integration:
	uv run pytest tests/integration -m "not live" --no-cov

db-up:
	docker compose up -d postgres
	until docker compose exec -T postgres pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done

db-down:
	docker compose down

db-reset: db-down db-up migrate

migrate:
	uv run alembic upgrade head
