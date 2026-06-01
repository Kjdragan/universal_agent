# Deploy System — First Full Run & Follow-Ups (2026-05-30)

**What this is:** 2026-05-30 was the first time the *entire* redesigned CI/CD chain ran end-to-end with all the new pieces in place — **PR → PR-Validate (now dependency-cached) → auto-merge → Deploy (now decomposed into a script) → Telegram ✅ deploy-complete notifier** — and every stage was live-verified against real production. This doc records what shipped, the proof it works, and the prioritized cleanup/improvement/dependency follow-ups learned along the way.

## Verified working (live, 2026-05-30)

| Stage | Evidence |
|---|---|
| Deploy-complete Telegram notifier (`deploy-notify.yml`) | Fired on the real deploy of `04ccb508`: logged `✅ Deploy OK — live SHA confirmed (deploy=04ccb508 live=04ccb508)`, `Telegram HTTP 200`. Sub-second; runs in a separate `workflow_run` *after* Deploy, so it adds **zero** latency to the deploy. |
| Deploy decomposition (`scripts/deploy/remote_deploy.sh`) | First deploy on the new script-pipe mechanism (`ce5daa6e`) succeeded in ~2 min; live `/api/v1/version` = `ce5daa6e` (Rule A). Deploy logic proven byte-identical to the prior heredoc. |
| PR-Validate dependency cache | Shipped (PR #583). Speedup to be confirmed across warm runs (see P2). |
| UA Telegram chat bot (`@ClaudeKevBot`) | End-to-end round-trip verified: operator "status" → auth → agent ran tools → reply in ~59s. Healthy; was idle 7+ days, not broken. |

## What shipped this session

- **PR #581** — `deploy-notify.yml` (truthful deploy-complete Telegram notifier; confirms live SHA per Rule A).
- **PR #582** — CI/CD ground-up evaluation + redesign proposal (`docs/operations/2026-05-30_cicd_ground_up_evaluation.md`).
- **PR #583** — uv dependency cache in PR-Validate (Phase 0b).
- **PR #586** — deploy.yml decomposition into `scripts/deploy/remote_deploy.sh` (Phase 1).
- **This PR** — replace the `actions/checkout` added in #586 with a `gh api` fetch of the single script file (kills the two annotations below).

## Follow-ups (prioritized)

### P1 — do soon

1. **Rotate over-exposed Telegram tokens.** The chat bot logs its `TELEGRAM_BOT_TOKEN` in plaintext in the getUpdates/sendMessage URL **every ~10s** to journald (httpx INFO). Separately, `CSI_REDDIT_TELEGRAM_BOT_TOKEN`'s value was inadvertently surfaced in an agent transcript on 2026-05-30. Recommend rotating both bot tokens and silencing httpx URL logging (`logging.getLogger("httpx").setLevel(WARNING)` in the bot entrypoint). Rotation is operator-gated (it touches live bots) — flagged, not done.
2. **Orphaned submodule gitlinks (repo hygiene).** Two paths are committed as gitlinks (mode `160000`) with **no `.gitmodules`**: `.claude/agents/agent-browser` and `test-remotion-project`. `actions/checkout`'s post-job submodule cleanup chokes on them (benign exit-128 warning). The files inside aren't actually tracked (only the gitlink SHA), so a fresh clone doesn't get them. Decide per path: `git rm --cached <path>` then commit the real files (if they should be vendored) or add to `.gitignore` (if they're local-only). Separate from CI/CD; do as a focused hygiene PR.
3. **GitHub Actions version drift / Node-20 deprecation.** `actions/checkout@v4` (Node 20, deprecated; forced to Node 24 on 2026-06-16) was added in #586 and is removed in this PR. Repo standard is already `@v6`. Remaining straggler: `actions/setup-python@v5` (×2) → bump to `@v6`. Recommend adding **Dependabot for `github-actions`** (`.github/dependabot.yml`, weekly) so action bumps are automatic instead of discovered via deprecation warnings.

### P2 — improvements (from the evaluation, PR #582)

4. **Confirm the uv-cache speedup** — compare 3 warm PR-Validate wall-clocks vs the ~5–6 min baseline; record the real number (per `feedback_verify_fix_before_shipping`).
5. **shellcheck CI gate** for `scripts/deploy/*.sh` (and ideally `scripts/*.sh`) in PR-Validate — now that deploy logic is a real script, lint it. (Note: actionlint did *not* catch the 2026-05-27 parser quirk; the real defense was keeping the YAML tiny, now done.)
6. **Consolidate branch policy** — the auto-merge / auto-rebase branch globs are duplicated across `pr-auto-merge.yml` and `pr-rebase-watchdog.yml`. Extract to one source of truth (composite action or shared step).

### P2 — Telegram redo (the operator-flagged "not usefully used" initiative)

7. **Latency** — every chat message spins a *full fresh agent session* (~59s for a trivial "status"). Add lightweight command handlers for common asks (`status`, `/help`, deploy status) that answer instantly without a full agent; reserve the full agent for real questions. **Note:** this is the *chat* bot only — it has **no bearing on deploy speed** (the deploy notifier is a separate sub-second curl).
8. **Startup noise** — downgrade the cosmetic `⚠️ External Gateway health check failed` startup warning (it's just boot-ordering during a deploy restart; the gateway is reachable seconds later).

## GitHub Actions dependency inventory (2026-05-30)

| Action | Version(s) in repo | Status |
|---|---|---|
| `actions/checkout` | `@v6` (5×) | Current (Node 24). `@v4` removed from deploy.yml in this PR. |
| `actions/setup-python` | `@v6` (1×), `@v5` (2×) | Bump the two `@v5` → `@v6`. |
| `astral-sh/setup-uv` | `@v6`, `@v5` | Both Node-24-era; standardize on `@v6`. |
| `tailscale/github-action` | `@v4` | Verify latest; bump if newer major exists. |

## Cross-references

- Evaluation & proposal: `docs/deployment/ci_cd_pipeline.md` + `docs/operations/2026-05-30_cicd_ground_up_evaluation.md`.
- Deploy decomposition mechanics: `ci_cd_pipeline.md` § "Deploy script decomposition (2026-05-30)".
- Memory: `project_2026-05-30_cicd_evaluation`, `feedback_verify_fix_before_shipping`, `project_2026-05-27_deployyml_parser_quirk`.
