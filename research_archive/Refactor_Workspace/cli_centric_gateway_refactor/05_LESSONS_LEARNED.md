# Lessons Learned / Gotchas

## Workspace guard wiring

- The workspace guard is wired into `hooks.py` in the `PreToolUse` chain.
- It runs **after** schema validation and **before** the ledger hook.
- It rewrites relative paths to absolute workspace-scoped paths.
- It blocks absolute paths that escape the workspace boundary.

## Fast path event emission

- SIMPLE queries previously skipped event emission entirely.
- Now they emit: `STATUS` (path=fast) → `TEXT` → `ITERATION_END`
- This ensures event parity between CLI direct mode and gateway mode.

## `setup_session` returns 6 values

- `setup_session(...)` returns:
  - `options, session, user_id, workspace_dir, trace, agent`

Any code unpacking fewer values will crash at runtime.

## Fast path vs tool loop

- SIMPLE queries may run via a fast path and may not emit the same events as the complex/tool loop.
- If event parity is required for all UIs, we should consider emitting minimal `STATUS`/`TEXT`/`ITERATION_END` events for fast path too.

## Packaging/imports for scripts

- Running scripts via `uv run` does not automatically include `src/` on `PYTHONPATH`.
- Fix is to install editable package:
  - `uv pip install -e .`

## Session metadata shapes vary

- `session.mcp` is not necessarily a dict; use `getattr` rather than `.get()`.
