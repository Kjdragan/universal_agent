# Testing Strategy

The Universal Agent project maintains high code quality through a rigorous testing suite covering unit, integration, and end-to-end scenarios.

## 1. Test Organization

Tests are located in the `/tests` directory and follow the project's source structure.

- `tests/memory/`: Vector store and tiered memory logic.
- `tests/gateway/`: API and session management.
- `tests/urw/`: Orchestrator and planning logic.
- `tests/durable/`: Checkpointing and resilience.

## 2. Test Markers

We use **pytest** markers to differentiate between local and cloud-dependent tests.

- **`@pytest.mark.llm`**: These tests require live connectivity to an LLM provider (Anthropic, Gemini). They are typically slower and incur costs.
- **Unit Tests**: Standard pytest functions (no marker) should run completely locally using mocks.

## 3. Running Tests

### Run all local tests

```bash
uv run pytest -m "not llm"
```

### Run a specific module

```bash
uv run pytest tests/memory/test_chromadb_backend.py
```

### Run with coverage

```bash
uv run pytest --cov=src/universal_agent
```

## 4. Mocking Strategy

### The unified `test_workspace` fixture

Most integration tests use a temporary workspace fixture that sets up:

1. A fresh `runtime.db` (Durable).
2. A local `memory/` scaffold.
3. Simulated environment variables.

### Mocking Tool Results

When testing the `UniversalAgent` logic without calling live APIs, we use `unittest.mock` to simulate `ClaudeSDKClient` responses, allowing us to verify the event-streaming and hook logic deterministically.

---

## 5. Continuous Integration (CI)

The project is configured for GitHub Actions, where tests are automatically run on every push.

- **Requirement**: Any PR modifying core execution logic (`agent_core`, `heartbeat`, `hooks`) MUST pass all non-LLM tests before the merge.
