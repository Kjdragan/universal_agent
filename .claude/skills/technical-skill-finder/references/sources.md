# Source Inputs

Use this precedence unless the user overrides. **Claude Code transcripts are the primary source.**

## Primary: Claude Code transcripts

1. `/home/kjdragan/.claude/projects/-home-kjdragan-lrepos-universal-agent*/*.jsonl`
   - One project dir per cwd: the main checkout plus one per `.claude/worktrees/*` (~75 UA dirs).
   - Files are `0600` (owner-only). One JSON object per line (JSONL).
   - Enumerate newest-first: `ls -t .../-home-kjdragan-lrepos-universal-agent*/*.jsonl`.

### Per-line schema (newer Claude Code format)

`type` values that matter for mining: `assistant`, `user`, `system`.
Bookkeeping `type` values to **ignore**: `agent-name`, `agent-setting`, `ai-title`, `attachment`,
`bridge-session`, `custom-title`, `file-history-snapshot`, `last-prompt`, `mode`, `permission-mode`,
`queue-operation`, `summary`.

Common envelope (on user/assistant/system lines): `uuid`, `parentUuid`, `timestamp` (ISO-8601 `Z`),
`cwd`, `gitBranch`, `sessionId`, `isSidechain`, `userType` (`external` on real user turns), `version`.

**Tool calls** live on `assistant` lines: `.message.content[]` is an array of items
`{type: "thinking"|"text"|"tool_use"}`. A `tool_use` item is
`{type:"tool_use", id:"toolu_…", name:"Bash"|"Edit"|"Read"|"Write"|"Agent"|…, input:{…}}`.
For Bash, `input.command` + `input.description`.

**Tool results / errors** live on the *next* `user` line, in two parallel representations:
1. `.message.content[]` item `{type:"tool_result", tool_use_id:"toolu_…", content:<string|array>, is_error?:true}`.
   `is_error` (snake_case) is **present only on failures**; there is no `isError` camelCase variant.
   `content` is usually a string, sometimes an array of `{type:"text",text:…}`.
2. A sibling top-level `.toolUseResult` (structured):
   - Bash success → `{stdout, stderr, interrupted, isImage, noOutputExpected}`.
   - Bash failure → a *string* prefixed `"Error: Exit code N…"`.
   - Edit/Write → `{filePath, structuredPatch, …}`; Read → `{file, type}`.
   So Bash stderr is reachable via `toolUseResult.stderr` (success) or the `is_error` content text
   (failure). Error text often contains ANSI codes (`[…m`) and `<tool_use_error>…</tool_use_error>`
   wrappers — strip both.

**Human vs synthetic prompts:** real human prompts are `user` lines where `.message.content` is a
plain **string**. Synthetic prompts are wrapped (`<task-notification>…`). Filter on `startswith` to
keep only genuine asks — these are the primary candidate-mining signal.

2. Repository-local instructions/telemetry: per-repo `CLAUDE.md` and `~/.claude/CLAUDE.md`, plus the
   existing skill library under `.claude/skills/` (used for dedupe in step 5).
3. MCP-exposed resources — only when explicitly authorized.

## Secondary (optional, consent-gated): `~/.codex`

Read only with explicit consent (`CONSENT_CODEX`). Codex uses a **different schema** — keep it behind
a separate adapter and label Codex-derived candidates.

- `~/.codex/history.jsonl` — flat prompt log: `{session_id, ts (epoch), text}` per line. Easiest
  "what did the user ask" feed.
- `~/.codex/sessions/YYYY/MM/DD/rollout-<ISO>-<uuid>.jsonl` — full per-session transcripts. Each line
  is `{type, timestamp, payload}` where top-level `type` ∈ `session_meta`, `turn_context`,
  `response_item` (the bulk — tool calls/outputs live in `payload`), `event_msg`. **Branch on
  `.payload.type`, not the top-level `type`.**
- `~/.codex/session_index.jsonl` — `{id, thread_name, updated_at}` (human-readable session names).
- Other Codex dirs (`skills/`, `archived_sessions/`, `logs_2.sqlite`, `state_5.sqlite`) are not needed.

## Consent / privacy precedence

- Default scope = Claude Code coding transcripts only.
- Any personal-signal / messaging source (Codex personal logs, messaging MCP resources) is read only
  after explicit approval, and its output stays isolated from coding-signal candidates unless the user
  asks for a merged scope.
