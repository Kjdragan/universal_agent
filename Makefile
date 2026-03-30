.PHONY: sync test test-unit test-integration test-file test-todo-pipeline build-ui dev-shell urw-smoke

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

test-todo-pipeline:
	uv run pytest tests/gateway/test_todo_dispatch_service.py tests/gateway/test_dashboard_agent_queue.py tests/test_task_hub_pipeline_repair.py tests/unit/test_todolist_dashboard_page.py -q

build-ui:
	cd web-ui && npm run build

dev-shell:
	./scripts/dev_shell.sh

urw-smoke:
	PYTHONPATH=src uv run python scripts/urw_smoke.py
