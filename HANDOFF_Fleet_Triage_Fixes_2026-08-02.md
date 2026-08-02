# HANDOFF — Fleet triage fixes (2026-08-02)

**For:** a fresh Claude Fable session in `~/lrepos/universal_agent`.
**Produced by:** a 24-agent verification sweep of 14h of fleet-alert email (2026-08-02 03:22Z–17:22Z). 59 issue candidates extracted, 42 verified against live VPS / DB / `gh` state. **Read-only** — nothing on the fleet was changed.
**Full exhibit (rendered, with all evidence):** https://uaonvps.taildcc090.ts.net/scratch/fleet-triage-2026-08-02/report.html
**Archived copy:** `scratch_archive/2026-08-02/132719__fleet-triage-2026-08-02/report.html`

---

## The prompt to run

> You are picking up a verified fix backlog. Read `HANDOFF_Fleet_Triage_Fixes_2026-08-02.md` in full before touching anything — it contains the evidence, the sequencing constraints, and an explicit list of things that look like bugs but are **not**. Every finding below was already proven against live production; you do not need to re-verify them, and re-verifying is the main way this work gets wasted. Work the tickets in the order given, ship each as its own PR under standing autonomy (branch from a freshly-fetched `origin/main`, PR, `gh pr merge --squash --auto --delete-branch`), and report what you shipped, what you deliberately skipped, and why.

---

## 1. Ground truth you can rely on (do not re-verify)

| Fact | Value |
|---|---|
| Prod SHA | `/opt/universal_agent` == `origin/main` == `7e396f54`, deployed 2026-08-02 11:59Z |
| Deploy pipeline | Healthy. Last 10 `deploy.yml` runs on `main` green. |
| VPS | `ssh ua@uaonvps`, always wrap remote commands in `bash -lc "…"`. `ua` has passwordless sudo. |
| VPS disk | **90.0%** — 173G/193G, 21 GiB free. CRITICAL threshold is 90%. |
| Desktop checkout | Stale and dirty, parked on a feature branch. **Judge landed-ness only against `origin/main` after `git fetch`.** Its `CLAUDE.md` is also stale and is injected into every desktop session — it manufactured one false finding already (see §5). |
| ZAI | Healthy: no pause, `consecutive_429s=0`. Sole provider, no failover (deliberate — see §4). |
| Infisical | Machine identity healthy; live universal-auth login returns 200, 359 secrets read cleanly. |

**Two repos are out of scope for this session:**
- `~/lrepos/demo_factory` — owned by another live session (`8f056529`). The nuggets build failure lives there. Do not touch it.
- `~/lrepos/dragan-plugins` — ticket 6 below lands there, and it has its own deployment rules. Load the `dragan-plugins-workflow` skill before editing, and **never** edit under `~/.claude/plugins/cache/`.

---

## 2. Tickets — ship in this order

Sequencing matters in exactly two places, both called out inline. Everything else is independent.

### T1 — Email triage parse is markdown-blind and the fallback silently upgrades trust
**File:** `src/universal_agent/services/email_task_bridge.py`, `src/universal_agent/hooks_service.py`
**Ship first — T9 depends on it.**

`_TRIAGE_FIELD_RE` (5 hand-copied anchored regexes) cannot match what the model actually emits. The LLM's routing verdict is discarded and replaced by manufactured defaults.

- **Proven:** 7 of 8 `hook_triage` rows in prod have `classification=''`, `priority=''`, `subject_summary=''`. 5 recorded `routing_decision='trusted_execute'` **purely from the fallback**. `auto_completed_non_action` has fired **zero times ever**.
- **Why it fails:** stored briefs use `**safety_status:** clean` and `- **safety_status:** clean`. The regexes anchor `^\s*<field>\s*:` and `\s*` cannot cross `**`.
- **Fix:**
  1. Rebuild all 5 patterns from one factory allowing an optional bullet `[-*•+]` and `[*_#]{0,3}` decoration; strip residual `**` from captures.
  2. `hooks_service.py` passes `sender_trusted=bool(metadata.get("sender_trusted", True))` — **change that default to `False`.** The key is absent in all 8 prod rows, so the default is doing real work.
  3. `safety_status` falls back to `'clean'` unless the literal substring `quarantine` appears. Record a `_fallback_fields` marker so a default is never mistaken for a verdict.
  4. Root-cause upgrade: make `_build_email_handler_prompt` require a fenced ` ```json ` envelope for the 5 routing fields and parse that first, with the regex as fallback.
  5. Tests using the two verbatim prod briefs recoverable from `activity_events.full_message`.

### T2 — YouTube tutorial hook loses videos; the retry path is structurally dead
**File:** `src/universal_agent/hooks_service.py` (+ `agent_setup.py` if you take the env route)

4 of the last 5 hook runs produced **zero artifacts**. Lost: `mi1flmYaLAk`, `ZFxh7sqbUZo`, `XwYPRLMLcNs`, `h5HLLIds53g`. The "retry attempt 2/3 has been queued" line in the alert is **false** — 0 of 4 retries ever acquired a session.

- **Proven:** `runtime_state.db` — Jul 19–30 = 23/23 attempt-1 `completed`; 08-01 = 3/3 `failed`, 08-02 = 1/2 `failed`, all `failure_class=hook_dispatch_failed`. Every attempt-2 sat `queued` with `provider_session_id` NULL for 60 min until the stale-orphan reaper killed it; attempt 3 never queued. Only one `manifest.json` newer than 07-31 exists under `resolve_artifacts_dir()`.
- **Producer is `src/universal_agent/scripts/youtube_daily_digest.py`**, not the playlist poller (that timer has been disabled since 07-31 and logged nothing).
- **Two stacked root causes:**
  1. `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` makes `Task`/`Agent` **fire-and-forget**, but `_build_manual_youtube_hook_action` *mandates* delegating via `Task(subagent_type='youtube-expert')`. The coordinator delegates, ends its turn in ~12s, the post-turn gate `_validate_youtube_tutorial_artifacts` finds no manifest and raises `youtube_artifacts_missing_manifest`; the backgrounded subagent dies with the session. The one survivor only worked because the coordinator redundantly did the work inline (19 tool calls).
  2. `_schedule_youtube_retry_attempt` fires `asyncio.create_task(self._dispatch_action(...))` **before** `_dispatch_action`'s `finally` pops `_youtube_video_dispatch_inflight`, so every retry is rejected by the video dedup guard (`inflight_age` 52–134s against a 3600s TTL). The guard *returns* `{"decision":"skipped"}` rather than raising, and the fire-and-forget call discards the return value, so nothing finalizes the attempt.
- **Fix:**
  - For (1): change `_build_manual_youtube_hook_action` to instruct inline `Skill: youtube-transcript-metadata` → `Skill: youtube-tutorial-creation` (replicating the surviving run), **or** thread a per-session `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=0` override into `agent_setup.py`'s `ClaudeAgentOptions.env` for this lane only.
  - For (2): add an explicit `skip_video_dedup: bool = False` parameter to `_dispatch_action` and short-circuit the video dedup guard on it; pass `True` from `_schedule_youtube_retry_attempt`. **Do not overload `skip_workflow_admission`** — different concerns. Also pop the inflight marker before scheduling, and retain the task handle with an `add_done_callback` that finalizes via `mark_needs_review` both on exception **and** on `result.get("decision") not in {"completed","started"}` (an exception-only callback would not have caught this).
  - **Do not weaken the artifact gate** — it caught real total loss.
  - Then re-POST the 4 lost video ids; their runs are terminal and the dedup keys are free.
- **Also:** `_classify_dispatch_failure` labels `youtube_artifacts_missing_manifest` as `hook_dispatch_failed`, which is in the retryable set. A deterministic validation error should route to needs_review — give it its own reason code (`youtube_artifacts_invalid`).
- **Not knowable:** what flipped this on 08-01. No `origin/main` change to `hooks_service.py` since 07-14, CLI unchanged since Jul 25. Best explanation is coordinator-LLM drift toward pure delegation. The fix is robust either way — don't spend time hunting it.
- **Dead code to delete while you're here:** `hooks_service.py::HooksService.finalize_stale_youtube_runs`. It is a superseded predecessor of `services/stuck_run_reaper.py::finalize_stale_youtube_hook_runs` (live, wired via `utils/db_health_monitor.py::check_stale_runs`, working — it finalized the alert's own run at 12:10:25Z). The name collision already cost one false HIGH-severity escalation.

### T3 — Alert emails silently swallow `<…>` and are an injection surface
**File:** `src/universal_agent/services/notification_dispatcher.py`

- **Proven:** delivered HTML for the external-email *security* alert reads `- Sender: Descope ``` while the plaintext part has `<team@hi.descope.com>`. The sender address — the single most important field in that alert — was parsed as an unknown HTML tag and dropped. Reproduced in a second alert. Source text is intact in the DB, so the loss is purely rendering.
- **Root cause:** `_format_email_html` imports `escape as _html_escape` and applies it to metadata, `final_response`, and `log_tail` — then returns `f"<p>{message}</p>"` **raw** (as are `{title}` and `{kind}`). For `kind=agentmail_review_required`, `message` is LLM prose summarizing an **untrusted external email**. An external sender who gets the triage model to echo an `<a href>` or tracking `<img>` has it rendered in Kevin's inbox.
- **Fix:** escape all three. **Escape *then* convert newlines** (`_html_escape(message).replace("\n","<br>")`, or wrap in `<pre style="white-space:pre-wrap">`) or you collapse a multi-line body to one line. Add tests mirroring the existing `final_response` escaping test — the body has no coverage today, which is why this survived.

### T4 — Alert emails strip the diagnostic fields
**File:** `src/universal_agent/services/notification_dispatcher.py`, `src/universal_agent/hooks_service.py`

`_EMAIL_CONTEXT_KEYS` is a global hardcoded allowlist grown ad hoc. It omits `reason`, `video_id`, `attempt_number`, `retry_count`, `max_attempts`, `hook_name`, `tutorial_title` — so the 11:08:19Z email rendered exactly three rows (session_id, run_id, workspace_dir) while the stored record carried `reason=hook_dispatch_failed` and `video_id=h5HLLIds53g`. `reason` is the discriminator that decides whether a video retries or is abandoned.

- **Fix:** (a) widen `_EMAIL_CONTEXT_KEYS` — safe, `_format_email_html` skips empty values; (b) more importantly, fold reason + video_id into the `message=` f-string in `hooks_service.py::_emit_youtube_retry_queued_notification` so they reach `summary`/`full_message` and survive Telegram, the plaintext part, the Gmail snippet, and the dashboard. Doesn't disturb dedup (`_scope_key_for_record` keys on run_id).

### T5 — Alert bodies are emailed twice, verbatim
**File:** `src/universal_agent/execution_engine.py`, `src/universal_agent/hooks_service.py`

- **Proven:** the stored `full_message` (3,693 chars) already contains `EMAIL TRIAGE BRIEF` twice — the doubling is upstream of the mailer, not a rendering bug. Reproduced in a second independent alert.
- **Root cause:** `execution_engine.py` deliberately re-emits the complete final response as `data={"final": True}` for non-streaming consumers. `gateway_server.py` filters it when streaming was seen; `hooks_service.py::_consume_gateway_execute` iterates the raw stream and appends **every** TEXT event. The dedup invariant was implemented in one consumer and never extracted.
- **Fix:** extract the predicate into a shared `execution_engine.py::is_redundant_final_text(event, saw_streaming_text)` and call it from **both** consumers, so a third consumer cannot repeat the bug.
- **Bonus:** `response_preview` is a join of the last 8 text events, not a preview — the name lies. Rename or fix.

### T6 — Daily 8 / Graph 8 briefs destroy numbers in bullets
**Repo:** `~/lrepos/dragan-plugins` — **load the `dragan-plugins-workflow` skill first.**
**File:** `skills/daily-eight-research-loop/scripts/research_loop.py::_fmt_points`

Kevin reads these daily. Every brief since 2026-07-20 has 1–4 bullets with the leading number eaten.

- **Proven from on-disk VPS source markdown (not from email):** `- % discounts`, `- + organisations`, `- ,000 scientists`, `- week validation period`, `- trillion parameters`. All five reproduced with the actual regex. `dragan-plugins` HEAD == `origin/main` == `fa314ac`, clean.
- **Root cause:** `re.sub(r"^[-*•\d.\s]+", "", ln)` — the greedy class includes `\d` and `.`, so it eats the bullet's own numeral until the first character outside the class.
- **Fix:** `re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", ln)`. The mandatory trailing `\s+` is what makes it safe. Fixes Graph 8 too (`graph_eight/loop.py` calls the same `de._append`).
- **Why this is worse than it looks:** `10-week validation period` → `week validation period` and `2.8 trillion parameters` → `trillion parameters` both stay grammatical. A reader cannot tell the number was destroyed.

### T7 — Graph 8 has never scraped an article; it has no fetch tool
**Repo:** `~/lrepos/dragan-plugins` · **File:** `graph_eight/loop.py::_finder`

8 highly specific claims per day rest entirely on search snippets, and the run reports success.

- **Proven:** today's on-disk briefs — Graph 8 = 0 "Full article scraped" / 8 "Not scraped"; Daily 8 on the same host 32 min later = 5 scraped. Graph 8's `metrics.json`: `appended=8, stop_reason=target_reached, selfcheck=true`.
- **Root cause:** `_finder` passes `allowed_tools=("WebSearch",)` — a one-tool surface. It never registers `research_loop::_tooling`, so `mcp__brief__read_page` does not exist in the node. The graph port inherited the coverage-*labelling* code without porting the coverage-*producing* stage. The label is truthful; the stage is missing.
- **Fix:** (1) build `de._tooling(cfg.topic)` once per run, pass it to `run_node`, add `mcp__brief__read_page` to `allowed_tools`, and add the Daily-8 step-4 instruction to `_finder_prompt` — **check first that `sdk_graphkit.run_node` accepts an `mcp_servers` arg.** (2) Independently, make `_selfcheck` fail or warn loudly when zero entries have `coverage=='full'`. **Ship (2) even if (1) slips** — silent success is the dangerous half.

### T8 — Watchdog: two bugs that page falsely and bounce healthy services
**File:** `scripts/vps_service_watchdog.sh`, `src/universal_agent/services/watchdog_restart_notifier.py`

Both live on `origin/main`, both untested.

- **(a) An empty `is-active` result reads as "down."** `check_service` does `active_state="$(systemctl is-active "$svc" 2>/dev/null || true)"`, discarding both stderr and the exit status and leaving an empty string that `!= "active"` treats as stopped. **Seven such restarts fired 08-01 19:41Z–08-02 00:05Z against services PID 1 proves were running continuously; one (api at 20:01) successfully bounced a healthy production service.**
  Fix: capture rc separately, add `timeout 10`, and on empty treat it as `probe_unavailable` → **skip the restart**, with a separate consecutive-probe-failure counter that escalates after N cycles so a genuinely wedged systemd still pages a human. No test in the suite exercises an empty result — add one.
- **(b) A failed restart command is reported as flapping.** `restart_service`'s failure branch passes the **literal `1`** for the escalation arg and the **literal `"failed"`** for post-state, so `watchdog_restart_notifier.py::_build_payload` renders `severity=error`, `requires_action=True`, `"Watchdog restarted (flapping)"` — next to the self-contradicting body "1x in the last 60m". Four phantom ERRORs were sent; all four services were healthy.
  Fix: pass `"$flapping"` and a real post-state probe (`restart_command_failed:${new_state:-probe_unavailable}`); give restart-command failure its own event branch in the notifier rather than borrowing the flap flag.
- **Already disproved — do not chase:** the "ledger not persisting" theory. `/var/lib/universal-agent/watchdog/*.restarts` are all present with correct epochs and every log line reads `flapping=0 restarts_in_window=0`.

### T9 — Two warning emails per untrusted inbound email, no bulk lane
**Depends on T1 — the `classification` field this needs is currently empty.**

Every untrusted inbound email fires two `severity=warning` operator emails 54s apart. Per-`kind` cooldown in `notification_dispatcher` structurally cannot merge them. There is no bulk/newsletter lane anywhere (`grep` for `List-Unsubscribe` / `Precedence` / `bulk` → zero hits). The classifier correctly said `fyi / p3 / "do not reply"` for a vendor newsletter and was ignored by a hard binary.

- **Volume reality check:** 3 pairs in 31 days. The alert's "alert fatigue" urgency is **not** supported — so do the merge and the classification-aware downgrade, and leave the bigger question (should arrival notices reach Kevin's inbox at all) for him.

### T10 — Tutorial builds never get marked completed, so the funnel cannot see output
**File:** `src/universal_agent/services/proactive_demo_nuggets.py`

The alert's "conversion stopped, 774 cancelled / 0 completed" is a **measurement artifact.** Tutorials did ship: 4 verified demos on disk Jul 29–Aug 1.

- **Root cause:** `run_zero_backlog_swipe` → `sweep_unbuilt_pending_builds` only wires the **negative** terminal transition; `TASK_STATUS_COMPLETED` never appears on this path. A built row is spared for one night via `keep_task_ids`, then cancelled by the next run — indistinguishable from a rejected candidate. (A Jul 8 backup had `completed=127`; the mid-July nuggets rework dropped the positive transition.)
- **Fix:** after `select_and_build_nuggets` returns and before the sweep, upsert each `built_summary['built']` row to `TASK_STATUS_COMPLETED` with the demo slug/path. `prune_settled_tasks(21d)` ages them out normally. Extend `tests/unit/test_proactive_tutorial_builds.py`.
- **Do NOT act on the alert's proposed remedy** (pause auto-creation) — it would starve the judge's candidate pool. Creation is ~14.5/day, not 48; open is 14, not 22; the judge gate is a cached, batched Haiku call, so the ZAI-budget premise is unsupported by `csi.db token_usage`.

### T11 — The end-of-day swipe discards judge-selected candidates whose builds failed
**File:** `src/universal_agent/services/proactive_demo_nuggets.py`

`run_zero_backlog_swipe` builds `keep_task_ids` solely from `built_summary['built']` — successful builds. It has no concept of "attempted but failed," so on a total-failure night the candidates the judge rated highest are guaranteed to be swept.

- **Blast radius is smaller than the alert claimed:** most swept rows *should* have been swept (the alert read the upstream CSI `score`, not the judge's verdict; the judge's own 08-02 scores were all under `_DEFAULT_MIN_SCORE = 7.0`). And it partly self-heals — `task_id` is a deterministic `sha256(video_id)` so CSI re-ingestion upserts rows back to `open` (5 of 13 came back the same day). **Residual real bug:** a build-worthy candidate whose source video ages out of the intel window is gone with no retry.
- **Fix:** accumulate failed-build task_ids in `select_and_build_nuggets` as `attempted_failed`; in `run_zero_backlog_swipe` do `keep = built_ids | attempted_failed`. `sweep_unbuilt_pending_builds` already honors `keep_task_ids`.
- **Do NOT** gate the whole swipe on `built != []` — a night where nothing clears 7.0 legitimately has empty `built` and should sweep.

### T12 — Nuggets health report cannot see "zero demos landed"
**File:** `scripts/nuggets_health_report.py`

This is why the nuggets failure hid for three nights. Three separate defects:

1. **No `builds_ok` rule.** `build_report` has no severity rule keyed on builds landed. Add before the return: if `run["builds_attempted"]` and not `run["builds_ok"]` → `severity = "error"` with `f"ZERO DEMOS LANDED — {run['builds_attempted']} attempted, all failed"`. Highest-value single line in this handoff.
2. **`_parse_run` under-counts failures.** It matches the literal `build FAILED rc=3`; the service emits `build FAILED rc=%d` for *any* nonzero rc, plus a `build subprocess raised` crash branch. Three rc=1 failures currently render as `builds_ok=3` — a perfect night. Generalize to `rc=(\d+)`, count the raised branch, keep an rc histogram in the body.
3. **`_land_history` is contaminated by manual builds.** It globs every `/home/ua/lrepos/demo-*/eval_report.json`, so a manual controlled build reset `days_since` to 0 thirty-three minutes *after* the nightly had landed nothing. Scope to `demo-proactive-*/eval_report.json`, or carry `last_land_nightly` separately and gate on that.

**Also — the alert is structurally guaranteed to fire again tomorrow for the wrong reason.** `build_report` escalates to `severity="error"` on `memory.events max > 0`. Under cgroup v2 that counter increments on *every successful reclaim* under a hard `memory.max`; with `MemoryHigh=infinity` and a 6 GiB cap on a job that peaks at 5.5 GiB, nonzero is the **expected steady state**, not a fault (`oom` and `oom_kill` were both 0). Split the loop: keep `error` for `oom`/`oom_kill`, demote `memory.events max` to informational (it is already in the rendered body). The honest throttling signal is `memory.events high`, already checked correctly.

**Memory itself needs no config change.** Peak 5.14 GiB against the new 6G ceiling looks alarming but `memory.events max` was 2706 at the 06:06Z mid-run sample and **still 2706** at teardown — zero limit hits after the ceiling was raised live. Worth adding while you're in the file: a headroom warning at `memory.peak/memory.max > 0.85` — note `_parse_run`'s regex doesn't capture `memory.max` today, so there is no denominator yet.

### T13 — `/healthz` is a static stub and is the ingester's only restart signal
**File:** CSI ingester `app.py::healthz`, `scripts/vps_service_watchdog.sh`

- **The alert's triggering incident was a false positive** — the "3.4h RSS stall" (07:30→11:00 UTC) is CT 02:30→06:00 and `schedule_fetch_hours` excludes CT 3/4/5. Ingest is healthy (122 events/24h, canary green).
- **But the structural claim holds:** `healthz` returns `{"status":"ok"}` unconditionally, the watchdog wires it as the sole trigger (heartbeat-file field empty), and the transcript canary is green-on-quiet by design — so a poller wedged inside a live uvicorn is un-restartable and invisible to both monitors.
- **Fix:** persist `_last_fetch_epoch` through the adapter's already-wired `set_state_backend` (this also kills a secondary bug: it is in-memory only, so 8 restarts on 08-02 caused back-to-back full 443-channel sweeps at 01:06/01:29/01:44); expose `csi.rss.seconds_since_fetch` via the existing `_metrics` Prometheus renderer; add a **new** `/livez` computed from the same schedule config and repoint the watchdog at it with generous grace. **Never change `/healthz`'s contract under the watchdog.**

### T14 — Ideation budget is spent on prompts, not tasks; dead by 10:05Z daily
**File:** `src/universal_agent/heartbeat_service.py`, `src/universal_agent/services/proactive_budget.py`, `reflection_engine.py`

- **Proven:** `proactive_daily_budget_counter = {"date":"2026-08-02","count":10}` at 10:05Z; every tick since ~10:16Z logs `daily_budget_exhausted`. **Zero** reflection/proactive_signal rows created 08-02.
- **Root cause:** the reflection branch calls `_increment_nightly_task_count(..., increment=1)` **unconditionally at prompt-injection time**, before Simone does anything. `proactive_budget.py`'s docstring claims it counts tasks created; the only increment site in the tree is that call. `reflection_engine.py` imports `increment_daily_proactive_count` and never calls it (dead import). Compounding: the ~1h pacer drips all 10 into 00:00–10:05 **UTC** = 19:00–05:05 CDT — entirely inside dormancy hours.
- **Fix:** split the keys — keep `proactive_daily_budget_counter` as a true task counter incremented from the task_hub insert path for `source_kind in ('proactive_signal','reflection')`, add `proactive_ideation_ticks` for pacing. Re-phase the pacer (`UA_PROACTIVE_IDEATION_MIN_INTERVAL_SECONDS` ≈ 8600s, or window to 06:00–22:00 `America/Chicago` per the dormancy policy in `project_docs/08_operations/03_dormancy_and_operating_hours.md`). Fix the lying docstring, drop the dead import, add a test asserting the counter only advances on an actual insert.

### T15 — `proactive_signal` lane: 3,436 artifacts, zero ever surfaced, by construction
**File:** `src/universal_agent/services/intelligence_reporter.py`

- **Proven:** `SELECT status, delivery_state, COUNT(*) … WHERE source_kind='proactive_signal'` returns **one row**: `candidate / not_surfaced / 3436`, spanning 2026-04-15 → today. Not one surfaced in 3.5 months. Still writing hourly.
- **Root cause:** `_rank_digest_artifacts` draws its pool via `list_artifacts(conn, limit=250)`, ordered `priority DESC, updated_at DESC`, with **no delivery filter**. There are 293 lifetime artifacts at `priority>=4` and they are never archived, so they permanently saturate the 250-row window. Every `proactive_signal` artifact is `priority<=3` → structurally unreachable.
- **Fix:** call `list_artifacts(..., delivery_state=DELIVERY_NOT_SURFACED, limit=250)` (the filter already exists in the signature) and reorder to `updated_at DESC, priority DESC`. Archive artifacts past `_STALE_MAX_AGE_DAYS`. Pair with a per-lane cap — 264/week of low-priority YouTube transcript insights into a 20-slot digest is a lot of noise.

### T16 — VPS disk: the cleanup jobs enumerate the big directory and discard it
**File:** `src/universal_agent/vp/worktree_utils.py`, `scripts/vp_coder_workspace_pruner.py`, `scripts/vp_coder_regenerable_reaper.py`
**Disk is at 90.0% — 0.6pp from CRITICAL.**

- The two `.venv`s the original alert named are already gone. **The blind spot moved:** `/opt/universal_agent/.claude/worktrees/fix-reflection-active-count-excludes-cron/.venv` = **6.7G** (torch + nvidia + triton, mtime 07-28), belonging to orphan commit `709db3ae` — on no remote branch, no PR. Both cleanup jobs enumerate it and discard it: `worktree_prune_roots()` defaults to `<repo>/.worktrees` only (`UA_WORKTREE_PRUNE_ROOTS` unset), and the reaper's root-2 is both disabled and excludes `.venv` by design. The daily reaper logged `Reaped 0 … 0.00 MiB` on all three of its last runs.
- **Fix (structural, yours):** default `worktree_prune_roots` to **both** `.worktrees` and `.claude/worktrees`; teach `prune_merged_worktrees` to distinguish "PR open" from "**no PR exists**" and age out the latter (today both are skip-forever); add a narrow `.venv` rule to the regenerable reaper gated on idle + no-open-PR + `live_process_inside()`.
- **Do NOT delete the orphan worktree** — that discards commit `709db3ae`. Deleting just its `.venv` is safe and regenerable but is **Kevin's call** (§4).
- **Worth flagging in your report:** the alert's root cause is too narrow. `/home/ua/lrepos` 31G, `/var/lib/containerd` 16G, huggingface cache 9.4G, `.claude-science/conda` 12G — all larger, none in any reaper's scope.

### T17 — Utilization telemetry is a hardcoded 0 (telemetry half only)
**File:** `src/universal_agent/heartbeat_service.py`

- **Proven:** `MAX(active_slots)=0` across **13,689 samples** since 2026-04-18, while 3,122 VP missions completed. `git grep acquire_slot` → **zero production call sites** for `CapacityGovernor.acquire_slot`. The sampler is healthy (141 samples/24h, `queue_depth` max 435 — that signal works); only its source is dead.
- **Fix (ship this half):** in the utilization block, source `active_slots` from real state — running `vp_missions` + in-progress `task_hub_items` — using the same query `proactive_activity_report.py` already uses.
- **Do NOT wire `acquire_slot` into the dispatch paths.** That activates a 2-slot cap that has silently never been enforced and would start throttling live dispatch. It is Kevin's call (§4). Note the second-order finding in your report: `can_dispatch()` Check 2 is unreachable, so `capacity_full` can never fire and `UA_CAPACITY_MAX_CONCURRENT` is an inert knob today.

### T18 — episode_ideas build failures report nothing but "status: build_failed"
**Repo:** wherever `showrunner/` lives (not `universal_agent` — locate it first)
**File:** `showrunner/report.py::_fmt_candidate`, `showrunner/__main__.py`

`showrunner/dispatch.py::collect_builds` persists exit code, log path, workspace, `manifest_status`, and verdict — and `_fmt_candidate` prints only `row['status']`. Two builds failed 08-01/08-02 and neither has been relaunched.

- **Fix:** parse `row['dispatch']` in `_fmt_candidate` for `build_failed`/`dispatch_failed` and emit the token line, `manifest_status`, and both paths — the data needs zero new plumbing. Add a one-line notify in `__main__.py`'s `watch` branch reusing `greenlight.py::_notify`.
- **Related, but owned by the demo_factory session — do not fix here:** both builds ran with the research stage skipped (`reason=no_auth`) because `build_demo.py` does no secret bootstrap.

### T19 — Small, trivial, ship as one PR
- **"Promoted: 0" is a metric with no producer.** `'promoted'` isn't in `VALID_CARD_STATUSES` and `record_feedback` would reject it — pinned at 0 forever. Change `proactive_intelligence_report.py::gather_pipeline_stats` to count `status IN ('actioned','tracking','approved')` and relabel. (The substantive half — that no card has ever reached any value-bearing state — is a lane-design question for Kevin, not a bug. Do **not** route it through the intel-auto-promoter: wrong lane, and that cron fires twice daily just to print `skipped:disabled_by_env`.)
- **Ideation report double-counts.** Says 40 when the unique queue is 22 — `get_stale_proposals` is a strict superset of `get_held_proposals` and `deliver_ideation_report` sums both. Subtract the intersection by `task_id` before counting/rendering; relabel "new" → "held". The backpressure branch already does this (`stale = []`) — the bug is recognized in one branch and not the other.
- **episode_ideas morning email builds broken YouTube links.** `showrunner/report.py::link_for` special-cases only `x:` and falls through to `youtube.com/watch?v={vid}`, so 21 `l30:<url>` rows render as `watch?v=l30:https://github.com/...`. `hotcards.py::_link` handles it correctly — two copies drifted. Hoist one `source_link(row)` resolver. Rendering-only; the dispatch seed carries the real URL. Same-root sibling: `report.py::calibration` miscounts every l30 candidate as YouTube.
- **Scout "4 sources dead" is a threshold bug, not dead sources.** `EMPTY_THRESHOLD=120` is one global run-count applied to sources with 3-minute and 30-day natural cadences. `hn:whoishiring` is a **permanent false positive by construction** — it will exceed 120 empty polls for ~29 days of every 30. The run-side watchdog already solved this correctly with time-based quiet detection after a false-positive day on 07-31; that fix was never carried into `scout/digest.py::build_digest`. Carry it.

---

## 3. Do NOT "fix" these — verified non-issues

Each of these was alerted on and each is wrong. Touching them makes things worse.

| Claim | Reality |
|---|---|
| `leftover_procs=3` — orphan leak not closed | The post-mortem script was counting itself. Fixed by `52baa888` (#1602); sha256-verified on the VPS; 14/14 guard tests pass; zero actual orphans. |
| `finalize_stale_youtube_runs` has no callers — "the safety net is fictional" | False. The alert grepped a dead near-duplicate. `stuck_run_reaper.py::finalize_stale_youtube_hook_runs` is live, wired, and finalized the alert's own run at 12:10:25Z. |
| `universal-agent-api` / `csi-ingester` "restart left it failed" | The units ran uninterrupted across both alerts (`NRestarts=0`). Fixed by `15da9ac7` (#1591) + `4e288960` (#1594). |
| Deploy exit-124 on `27304f33` | Ten later `main` deploys all green; prod == `origin/main`. Same two commits. |
| Infisical machine identity expired | Live login returns 200 with a fresh 30-day token, 359 secrets read. Self-healed. |
| Stale timers graded healthy | The four flagged units are weekly/monthly with valid `NEXT`; `proactive_activity_report.py::_classify_systemd` is cadence-aware and **was correct**. Adding staleness there would create false degradeds. |
| 79 parked `vp_mission`s need triage | `parked` is a **terminal** status, the canonical retire verb. All 79 were bulk-dispositioned 2026-07-23; `prune_settled_tasks` deletes them ~2026-08-13. Documented recurring false positive. |
| Cron spawn-timeout defect class unswept | `d48c6f8c` (#1515) bound a *code path*, not a job — the wrapper is in the generic `lightweight` branch of `_run_job`, so all 11 lightweight crons were already covered. |
| 4 scout sources dead | None are. Two had already recovered with new rows; HN's August thread isn't posted yet; r/slavelabour matches the ledger to 3 minutes. |
| Enrich-button signatures mangled | False. `render_email.py::enrich_link` and `email_send.py` are correct. **Do not touch the enrich gateway** — see the next row. |
| Cody mode doc divergence | Code and `CLAUDE.md` have agreed on `zai` since `70e60732` (#1129), 2026-06-21. The "divergence" was the stale desktop checkout. |

### The one that will mislead you if you don't know it

**The Gmail MCP double-decodes quoted-printable on read.** Decisive control on message `19fc29402b0f812c`: Gmail's server-generated `snippet` reads `peak=5516083200 swap_peak=79826944 swept=13`, while the MCP's `plaintextBody`/`htmlBody` for the *identical* message read `peakU16083200 swap_peaky826944 swept\x13`. `quopri.decodestring` reproduces the delta 5/5. Any `=` followed by two hex chars is destroyed — timestamps, byte counts, SHA-256 signatures, URL params.

**Consequences:** (a) do not file a bug with AgentMail and do not "fix" the encoders — they are correct; (b) treat any number read out of a Gmail-MCP body as untrusted, prefer `snippet` or re-derive from files on disk; (c) if you find the fleet-alert generator and it quotes MCP-decoded bodies, its numeric claims are subject to the same corruption. It was not located during triage — worth finding.

---

## 4. Blocked on Kevin — report, do not act

- **Rotate the GitHub PAT embedded in the prod git remote URL.** `git remote -v` in `/opt/universal_agent` prints `https://x-access-token:<token>@github.com/…` with no credential helper. Any agent or transcript that dumps remotes leaks a repo-write credential. After rotation the fix is a deploy key. Rotation touches an external account and can break the deploy mid-flight.
- **Turn off the Gmail → `kevin.dragan@outlook.com` forward** (Settings → Forwarding and POP/IMAP; also check Filters for a "Forward to" action). Only Kevin can. Severity is lower than the alert framed it — 1 DSN in 24h, and the **fleet-alert channel is unaffected**. No UA code path targets that address.
- **Reclaim the 6.7 GB orphan `.venv`** (T16) — safe and regenerable, but it is a delete on prod.
- **The 3 open `codie/*` PRs.** All clean and green; auto-merge exclusion is deliberate 2026-05-17 policy with a working disposition path (`codie-pr-review-queue.yml` → issue #986, 14-day auto-close). **Do not add `codie/*` back to auto-merge.** Either drain them or raise `UA_CODIE_PR_CLOSE_DAYS` — #1395 aged out on 07-27 and its work was discarded.
- **ZAI has no failover; on error 1310 the fleet stops.** Confirmed in `zai_control.py::handle_weekly_exhaustion` → `set_global_pause`. This is an unmade cost decision, not an oversight — #795 left Anthropic on 2026-06-07 precisely because it API-bills the Max SDK path. Cheapest first move: a `notify-phone` push on a fresh `weekly_exhaustion` stamp. Note the weekly-budget meter already false-positived once in prod (2026-07-24), which argues against auto-escalation on that signal.
- **Wiring `acquire_slot`** (T17) — activates a never-enforced 2-slot cap.
- **Whether external-email arrival notices should reach his inbox at all** (T9).

---

## 5. House rules for this session

- **Branch discipline:** `git fetch origin` then branch from `origin/main` — never a bare `git checkout -b` off local `main`. The desktop checkout is dirty and parked; if `git status` is dirty, use `git worktree add <tmp> origin/main`, build there, remove the worktree after, and leave the original checkout on the branch and files you found it on. Before opening any PR, `git rev-list --count origin/main..HEAD` must equal the number of commits you actually made.
- **One PR per ticket**, `claude/<task>` naming, then `gh pr merge <n> --squash --auto --delete-branch`. Never `--admin`, never a CI bypass. PRs are gated by `.github/workflows/pr-validate.yml`.
- **Docs ship in the same PR as the behavior change.** Find the canonical docs that own the code you touched via their `code_paths:` frontmatter and update them now — do not defer to the nightly sweep:
  ```bash
  git diff --name-only origin/main...HEAD | while read f; do
    grep -rl -e "$f" -e "$(dirname "$f")" project_docs --include='*.md'
  done | sort -u
  ```
- **Cite with symbols (`file.py::symbol`), never line numbers.** CI enforces this.
- **Read before you propose.** `CLAUDE.md`'s pre-implementation table maps verbs to the canonical service module. Task claiming, stale-run recovery, timeouts, cron registration, and artifact path resolution all have one canonical home — compose with it, don't hand-roll.
- **Timeouts:** use the idle / no-progress watchdog (`timeout_policy.py::LivenessWatchdog`), never a bare wall-clock cap. A wall-clock cap killed live Simone work turns on 2026-06-14.
- **Nothing operational runs on the desktop.** Anything recurring is a `deployment/systemd/` unit deployed to the VPS.
- **The desktop checkout's `CLAUDE.md` is stale** and is injected into every desktop session — it already manufactured one false finding. Reconcile it via a worktree off `origin/main`; do not force anything over the dirty tree.

---

## 6. Report back

State plainly: what shipped (PR numbers + merge status), what you skipped and why, anything in §3 you believe was misjudged (with the evidence that changed your mind), and anything new you found. If a deploy goes red, fix forward or roll back yourself — don't leave it broken.
