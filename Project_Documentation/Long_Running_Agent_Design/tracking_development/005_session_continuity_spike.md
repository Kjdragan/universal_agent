# 005: Session Continuity Spike (Resume/Fork)

Date: 2026-01-02
Goal: Identify where to plug resume/fork, then probe if server-side session state exists.

## A1) Resume/Fork Knobs and Where to Plug Them

SDK knobs (ClaudeAgentOptions):
- continue_conversation: bool
- resume: str | None
- fork_session: bool

SDK behavior (CLI flags):
- continue_conversation -> `--continue`
- resume -> `--resume <session_id>`
- fork_session -> `--fork-session`
Source: `src/claude_agent_sdk/_internal/transport/subprocess_cli.py` in the SDK repo.

Where we would set these in our repo:
1) `src/universal_agent/main.py` in `setup_session()` where `ClaudeAgentOptions(...)` is instantiated.
   - Path: `src/universal_agent/main.py`
   - Location: `options = ClaudeAgentOptions(...)` in `setup_session()` (around the primary system prompt build).
2) `src/universal_agent/agent_core.py` in `AgentCore.initialize()` where `ClaudeAgentOptions(...)` is set for the bot/runtime agent.
   - Path: `src/universal_agent/agent_core.py`
   - Location: `self.options = ClaudeAgentOptions(...)` inside `AgentCore.initialize()`.

Where session IDs / resume tokens come from:
- SDK `ResultMessage` includes `session_id`.
  - Source: `src/claude_agent_sdk/types.py` in SDK repo.
  - Field: `ResultMessage.session_id`.

Do we store them today?
- No. We do not currently persist the SDK session_id in our runtime DB or trace. It is not captured in `trace.json`, nor stored in `runs` table. The only "session_id" used in our code is the Composio session id (for workbench / MCP), which is unrelated to the Claude CLI session resume token.

Where to persist if we decide to use resume/fork:
- Runtime DB `runs` table (add provider_session_id / resume_token).
- Trace record (`trace["session_id"]` or similar).
- `Project_Documentation/Long_Running_Agent_Design/KevinRestartWithThis.md` (optional: add session_id if resume works).

## A2) Session Continuity Probe Script

Script added:
- `scripts/session_continuity_probe.py`

What it does:
- Initial mode:
  - Sends: “Remember this nonce: NONCE=<uuid>. Reply only with ‘ACK <nonce>’.”
  - Then asks: “What nonce did I ask you to remember?”
  - Saves `session_id` and `nonce` to `AGENT_RUN_WORKSPACES/session_continuity_probe_state.json`
- Resume mode:
  - Uses `resume=<session_id>` + `continue_conversation=True`
  - Asks: “What nonce did I ask you to remember?”
  - Passes only if response includes the nonce without re-supplying history
- Fork mode:
  - Branch A: resume base session, store “Preference A”
  - Branch B: resume base session with `fork_session=True`, store “Preference B”
  - Verifies each branch recalls its own preference

How to run:
1) Initial run:
   `PYTHONPATH=src uv run python scripts/session_continuity_probe.py --mode initial`
2) Resume run:
   `PYTHONPATH=src uv run python scripts/session_continuity_probe.py --mode resume`
3) Fork run:
   `PYTHONPATH=src uv run python scripts/session_continuity_probe.py --mode fork`

Expected outputs:
- PASS/FAIL for ACK and recall in initial run.
- PASS/FAIL for resume recall in resume run.
- PASS/FAIL for branch A and branch B in fork run.

## Run Log (Initial Attempt)
- Result: FAIL (model not found)
- Error: "model: claude-3-5-sonnet-20241022" (404)
- Action: Re-run with `--model` set to a valid backend model (e.g., use `MODEL_NAME` or `ANTHROPIC_DEFAULT_SONNET_MODEL`).

## Run Log (Initial Attempt with Valid Model)
- Result: FAIL (recall within same process)
- Observation: ACK succeeded, but recall responded with no prior context.
- Cause: probe used a new client per query (separate sessions) so memory did not persist.
- Fix: updated `scripts/session_continuity_probe.py` to reuse a single `ClaudeSDKClient` for both prompts in initial mode.

## Run Log (Initial Attempt After Fix)
- Result: PASS (same-process continuity)
- ACK response: `ACK 45f18d0e-14fe-4cdc-8b91-1d847c6494a8`
- Recall response: `The nonce you asked me to remember was: 45f18d0e-14fe-4cdc-8b91-1d847c6494a8`
- Session ID: `7f436a7c-c5ca-4344-82d2-d29ce5bef91b`

## Run Log (Resume Attempt)
- Result: PASS (server-side resume worked)
- Resume session: `7f436a7c-c5ca-4344-82d2-d29ce5bef91b`
- Response: `45f18d0e-14fe-4cdc-8b91-1d847c6494a8`
- Returned session ID: `7f436a7c-c5ca-4344-82d2-d29ce5bef91b`

## Run Log (Fork Attempt)
- Result: PASS (branching works)
- Base session: `7f436a7c-c5ca-4344-82d2-d29ce5bef91b`
- Branch A: Response `Preference A`
- Branch B session: `a38eb712-2d78-4c3a-88c4-fd1975f1a8b4`, Response `Preference B`

## A3) Strategy Decision (Pending Probe Results)
Conclusion: server-side session state exists for this backend and supports resume + fork.
Recommended strategy:
- Persist SDK session_id (provider session handle) in runtime DB per run.
- On resume, pass `continue_conversation=True` and `resume=<session_id>` to ClaudeAgentOptions.
- Use `fork_session=True` for explicit branching if needed (e.g., alternate report variants).
- Still keep resume packet + compacted transcript as fallback if the provider session expires.

## Notes
- This probe intentionally does not use local transcript rehydration. It purely tests upstream session state.
