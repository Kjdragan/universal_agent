# AGENTS.md — Codex / OpenAI / Antigravity Agents

**Read [`CLAUDE.md`](CLAUDE.md) first.** All general rules for this repository — problem-solving philosophy, code-verified answers, LLM-native intelligence design, SSHFS, git workflow, Claude execution environments, working rules, operating hours, pre-implementation reading, production verification, documentation maintenance, implementation plan standards — apply to you exactly as they do to Claude Code.

The sections below are **additional** Codex-specific rules that apply when Codex is the active agent (PR review, browser-based debugging). They are not a replacement for CLAUDE.md; they are a delta on top of it.

---

## Codex Review Guidelines

These guidelines apply when Codex reviews pull requests targeting `main` (the only deploy-firing branch post-2026-05-10) and `feature/latest2` (Kevin's working pseudo-trunk):

- Flag any code that logs, stores, or transmits PII or secrets without explicit redaction.
- Verify that every new or modified API route is wrapped by the appropriate authentication/authorization middleware.
- Flag blocking I/O (database calls, HTTP requests) that runs inside an async event loop without `await` or proper executor offloading.
- Verify that background tasks and service loops handle exceptions so they don't silently die.
- Flag Python code that imports secrets or API keys from environment variables directly instead of using the Infisical secret service (our canonical secrets provider — never `.env` files or `os.getenv` for secrets).
- Flag changes that touch `.github/workflows/deploy.yml` if the corresponding canonical docs in `docs/deployment/` were not updated in the same PR.
- Do not flag formatting-only issues (whitespace, line length) unless they break a linter gate.
- Treat typos in user-facing strings or documentation as P1.

## Codex Browser Debugging Rules

When working on frontend bugs, local web apps, or browser-based verification:

1. Use the browser MCP tools instead of guessing.
2. Start by navigating to the local app URL.
3. Reproduce the bug in the browser.
4. Inspect screenshots and page state.
5. Inspect failed network requests if relevant.
6. Only then edit code.
7. After edits, retest in the browser to confirm the fix.

Do not claim a UI bug is fixed unless it has been verified through the browser tools.
