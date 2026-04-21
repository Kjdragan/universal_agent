# Repository Agent Rules

## Problem-Solving Philosophy

When investigating or fixing issues, always solve the **root cause** holistically — never just cure symptoms with band-aids. Before implementing a fix, ask:

1. **Can we expand capabilities** rather than restrict them? (e.g., raise a system limit rather than cap our data to fit under it)
2. **Is there a proper architectural pattern** for this? (e.g., write large data to files instead of stuffing it into env vars)
3. **Are we losing information or functionality** with this approach? If yes, find a better way.

Defensive guards and safety nets are acceptable as a *last resort backstop*, but they must not be the primary fix. The primary fix should eliminate the problem at its source.

## Code-Verified Answers

When answering questions about how this system works — architecture, data flows, service interactions, agent pipelines, or any behavioral claim — you **MUST read the actual source code first** before responding. Do not answer from memory, assumptions, or general knowledge.

**Mandatory process:**

1. **Read before you speak.** If the user asks "how does X work?", open and read the relevant source files before forming your answer. Use `grep_search`, `view_file`, and `find_by_name` to locate the code.
2. **Cite what you find.** Reference specific files, functions, and line numbers that support your explanation. If you cannot point to actual code, say "I need to check the code" — do not guess.
3. **Never fabricate pipeline steps.** This system has complex multi-agent pipelines (email triage, heartbeat dispatch, daemon sessions, VP orchestration). These have specific intermediaries, classifiers, and routing logic. Do not simplify or omit steps you haven't verified exist or don't exist.
4. **Distinguish what you know from what you're inferring.** If you've read the code and it's clear, state it with confidence. If you're extrapolating beyond what the code shows, explicitly flag it as an inference.
5. **When in doubt, investigate more.** It is always better to spend an extra 30 seconds reading code than to give a wrong answer that wastes the user's time and erodes trust.

**Why this matters:** A confident but incorrect architectural explanation is worse than saying "let me check." The user relies on accurate descriptions of their own system to make decisions. Wrong answers about agent pipelines, email flows, or session lifecycle can lead to flawed design decisions downstream.

## Deployment Contract

This repository has exactly one supported application deployment path. Production is branch-driven and automated; **do not recommend or use ad hoc `ssh`, `rsync`, `git pull`, or manual VPS deployment flows**.

1. **Integration:** Open reviewed feature work against `develop`. Pull requests into `develop` are the single Codex review gate, and `develop` itself does not deploy.
2. **Production:** Fast-forward the validated `develop` SHA to `main`. The push to `main` triggers the single automated deploy workflow and updates production on the VPS checkout at `/opt/universal_agent` (or `/opt/universal_agent_repo` as a fallback).

**Canonical deployment docs:**

- `docs/deployment/architecture_overview.md`
- `docs/deployment/ci_cd_pipeline.md`
- `docs/deployment/infisical_factories.md`

**Canonical CI/CD workflow file:**

- `.github/workflows/deploy.yml`

**CI/CD Access:**

- Tailscale CI access: GitHub Actions -> `TAILSCALE_OAUTH_CLIENT_ID` + `TAILSCALE_OAUTH_SECRET` -> tag identity `tag:ci-gha`.

**Legacy artifacts:**

- `scripts/vpsctl.sh` is a break-glass diagnostics helper, not the normal deployment path.

**Production URL:**

- `app.clearspringcg.com` is the prodcution url, so use this when you are using a browser to diagnose issues with the application

## Documentation

All documentation for this project MUST reside exclusively within the `docs/` directory. Creating any other documentation directories (such as `OFFICIAL_PROJECT_DOCUMENTATION/`) is strictly prohibited.

When asked to update or create documentation:

1. **Always Check the Indexes First**: You must consult `docs/README.md` and `docs/Documentation_Status.md` before proceeding.
2. **Update Over Create**: If a document already exists for your topic, update the existing file rather than creating a new one.
3. **Log New Documents**: If you must create a new file, you are required to add a link and description of that new file to both `docs/README.md` and `docs/Documentation_Status.md`.
4. **No Unindexed Files**: No document should exist in `docs/` without being linked from one of the two index files.

### Dynamic Documentation Maintenance (MANDATORY)

Documentation updates are **not optional follow-up work** — they are part of the implementation itself. When you make code changes that affect system behavior, architecture, routing, protocols, or configuration:

1. **Update docs during implementation, not after.** Treat documentation updates as a deliverable of the same work unit, not a separate task.
2. **Identify affected docs before coding.** Check `docs/README.md` and `docs/Documentation_Status.md` to find which existing documents cover the areas you are changing. Read them before you start coding so you understand the documented contract.
3. **Update canonical source-of-truth docs first.** If your change touches email routing, update `82_Email_Architecture`. If it touches VP delegation, update `03_VP_Workers_And_Delegation`. If it touches Task Hub, update `107_Task_Hub_Master_Reference`. Always update the canonical doc, not a peripheral reference.
4. **Include visual artifacts.** Mermaid diagrams, routing tables, and code-verified citations in doc updates — not just prose paragraphs.
5. **Update both indexes.** Any new doc must appear in both `docs/README.md` and `docs/Documentation_Status.md`. Existing doc updates should bump the "last updated" timestamp.
6. **When in doubt, update.** If you are unsure whether a change is "significant enough" to warrant a doc update, it is. Architecture drift caused by undocumented changes is worse than a minor redundant doc update.

## Implementation Plan Quality Standards

Implementation plans are decision documents — they must make complex system flows understandable at a glance. Text-only explanations are insufficient for this codebase's multi-agent architecture.

**Every implementation plan MUST include:**

1. **Mermaid sequence diagrams** for any multi-component interaction (email flows, task dispatch chains, agent delegation). Show the actual participants, message payloads, and decision points.
2. **Mermaid flowcharts** for routing/branching logic (e.g., "which inbox → which agent → which action").
3. **Code-verified citations** with `file:///path#Lnnn` links to the actual source lines that support each claim. Do not describe system behavior without pointing to the code that implements it.
4. **Summary tables** for change impact ("What Changes vs. What Stays"), communication patterns, or comparison of alternatives.
5. **Concrete code snippets** for every proposed modification — show the actual function signatures, new helper functions, and prompt text changes.
6. **Phase-by-phase breakdown** with clear boundaries between config-only changes, code changes, and prompt changes.

**Why this matters:** This system has complex multi-agent pipelines where a wrong mental model leads to flawed design decisions. Visual artifacts (diagrams, tables) catch misunderstandings that paragraphs hide.

## Review guidelines

These guidelines apply when Codex reviews pull requests targeting `develop`.

- Flag any code that logs, stores, or transmits PII or secrets without explicit redaction.
- Verify that every new or modified API route is wrapped by the appropriate authentication/authorization middleware.
- Flag blocking I/O (database calls, HTTP requests) that runs inside an async event loop without `await` or proper executor offloading.
- Verify that background tasks and service loops handle exceptions so they don't silently die.
- Flag Python code that imports secrets or API keys from environment variables directly instead of using the Infisical secret service (our canonical secrets provider — never `.env` files or `os.getenv` for secrets).
- Flag changes that touch `.github/workflows/deploy.yml` if the corresponding canonical docs in `docs/deployment/` were not updated in the same PR.
- Do not flag formatting-only issues (whitespace, line length) unless they break a linter gate.
- Treat typos in user-facing strings or documentation as P1.

If you are the codex agent, then: """# Browser debugging rules

When working on frontend bugs, local web apps, or browser-based verification:

1. Use the browser MCP tools instead of guessing.
2. Start by navigating to the local app URL.
3. Reproduce the bug in the browser.
4. Inspect screenshots and page state.
5. Inspect failed network requests if relevant.
6. Only then edit code.
7. After edits, retest in the browser to confirm the fix.

Do not claim a UI bug is fixed unless it has been verified through the browser tools."""
