.PHONY: sync test test-unit test-integration test-file dev-shell urw-smoke

sync:
	uv sync

test:
	uv run pytest

test-unit:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration

test-file:
	@if [ -z "$(FILE)" ]; then \
		echo "Usage: make test-file FILE=tests/unit/test_tool_schema_guardrail.py"; \
		exit 1; \
	fi
	uv run pytest $(FILE)

dev-shell:
	./scripts/dev_shell.sh

urw-smoke:
	PYTHONPATH=src uv run python scripts/urw_smoke.py
