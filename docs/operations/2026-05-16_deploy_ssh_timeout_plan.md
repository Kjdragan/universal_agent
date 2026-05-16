# Deploy Workflow SSH-Timeout False-Failure — Remediation Plan

> **Status (2026-05-16 PM update):** **Options A + B shipped.** Option C deferred per operator decision pending ~5-deploy observation window. See § 10 for implementation pointers.
> **Author:** Claude Opus 4.7 via PR drafting session 2026-05-16.
> **Triggering incident:** PR #300 deploy (run `25967376397`) reported `failure` exit 255 at 16:47:43, but VPS verification confirmed services restarted at 16:52:10 with the correct merged SHA `2225d48c`. Identical pattern observed on PR #297 and #299 in the same week.

---

## 1. Symptom

`deploy.yml` workflow exits red (`exit code 255`), GitHub shows the run as failed, but the production VPS is correctly running the new code at the merged SHA.

Operator/agent cost:
- Every CI-status alert becomes ambiguous — was this a real failure, or another SSH timeout?
- Per CLAUDE.md Rule A we must SSH the VPS, check `/api/v1/version`, confirm the SHA, and check `systemctl is-active` on every deploy — manually.
- Signal-vs-noise on the `[ERROR] deploy.yml failed` notification degrades to the point of being ignored, which means a *real* deploy failure could go unnoticed.

## 2. Reproduction signature (from PR #300 run)

```
16:44:34  Next.js build completed (all routes enumerated)
16:44:35  MkDocs build completed
16:44:42  --> Setting deployment-window flag...
16:44:42  Installed unit: /etc/systemd/system/universal-agent-gateway.service
          [5 more `Installed unit:` lines]
16:44:42  Installed drop-in: /etc/systemd/system/universal-agent-api.service.d/stack-limit.conf
                              ^^^ last visible log line
          [ 3-minute silent gap — nothing emitted via SSH ]
16:47:43  --> Installing canonical VP worker unit template from repo...
16:47:43  Terminated
##[error]Process completed with exit code 255.
```

The 3-minute silence between `16:44:42` and `16:47:43` is `scripts/install_vps_systemd_units.sh` finishing its `systemctl daemon-reload` step and the post-install validation pass. No output flows through SSH during that window.

The follow-up step (`Installing canonical VP worker unit template from repo`) prints once, then immediately the SSH channel is `Terminated` — the connection has been dropped between the two echoes by an idle-timeout-killer somewhere in the path. The deploy script's `set -euo pipefail` then propagates a fatal exit because the connection closing looks like a SIGTERM to the remote shell.

**Critical fact:** the silent step *also* triggers `sudo systemctl daemon-reload` followed by service restarts. Those restarts complete on the VPS independently of the SSH session — which is why the version endpoint reports `process_started_at: 2026-05-16T16:52:10` (5 minutes after the SSH disconnect, consistent with the restart cascade finishing).

## 3. Root cause hypothesis (ranked)

| # | Hypothesis | Evidence for | Evidence against |
|---|---|---|---|
| H1 | SSH client-side idle timeout — no `ServerAliveInterval` set, so `ssh` doesn't send keepalives during long silent remote steps; an intermediary NAT/firewall drops the idle connection. | 3-min silence is the *exact* idle window where the kill happens. Default SSH has no application-level keepalive. Same pattern across PR #297, #299, #300 — all on long install steps. | None — this is the most consistent explanation. |
| H2 | `appleboy/ssh-action` internal timeout (not relevant here — we don't use it). | n/a | We use plain `ssh` in a heredoc, line 97 of `deploy.yml`. |
| H3 | The Tailscale ephemeral GHA node's connection state has aggressive idle eviction. | Plausible — Tailscale magic-routing is the path. | Even if true, the SSH-level fix (H1's remedy) sends application packets that prevent any layer's idle-eviction logic from firing. |
| H4 | Memory pressure on the VPS kills the SSH child process. | None — VPS memory is fine; services restarted cleanly after the workflow died. | The "Terminated" log line is consistent with SSH connection drop, not OOM. |

**Conclusion:** H1 is the load-bearing cause. The path is `GHA runner → Tailscale subnet → VPS sshd → bash heredoc`, and somewhere along that path an idle connection longer than ~3 minutes gets pruned.

## 4. Remediation options

### Option A — SSH keepalive flags (smallest possible fix)

Add `-o ServerAliveInterval=30 -o ServerAliveCountMax=120` to the `ssh` command at `deploy.yml:97`. SSH will emit a keepalive packet every 30 seconds and tolerate up to 60 minutes of total idle time. This keeps TCP traffic flowing during silent remote steps and prevents any layer's idle-killer from firing.

- **Diff size:** 2 flags, one line change.
- **Risk:** Near-zero. These flags only affect how SSH handles idle connections; they do not change behavior when output is flowing.
- **Rollback:** Revert the line.
- **Doesn't fix:** Real connectivity failures, real script errors, real timeouts >60 min.

### Option B — Stream progress from silent script (defense in depth)

Modify `scripts/install_vps_systemd_units.sh` and any other long-silent step to emit a heartbeat line every ~30s:

```bash
( while true; do echo "[heartbeat] install_vps_systemd_units still running at $(date -Iseconds)"; sleep 30; done ) &
HB_PID=$!
trap "kill $HB_PID 2>/dev/null || true" EXIT
# ... actual install work ...
```

- **Diff size:** ~10 lines per silent script.
- **Risk:** Low. Heartbeat output is informational only.
- **Wins:** Operator/agent gets visible progress in the workflow log; layered defense against any keepalive-evasion path.
- **Doesn't fix:** Real disconnects. (Option A is still needed.)

### Option C — Fire-and-poll deploy (largest refactor, most robust)

Restructure the deploy step so the GHA job no longer holds a single long-lived SSH session. Instead:

1. First SSH call: `nohup bash /opt/universal_agent/scripts/deploy.sh > /tmp/deploy.log 2>&1 & echo $! > /tmp/deploy.pid; disown`
2. Subsequent SSH calls (every 30s): `tail -n +$NEXT_LINE /tmp/deploy.log; ps -p $(cat /tmp/deploy.pid) >/dev/null && echo RUNNING || echo DONE`
3. Final SSH call: `cat /tmp/deploy.exit_code`

Each SSH call is short-lived (<10 seconds) so no idle-timeout can ever fire. The deploy script runs detached on the VPS to completion regardless of GHA-runner state.

- **Diff size:** Substantial refactor of `deploy.yml` + a new `scripts/deploy.sh` on the VPS.
- **Risk:** Medium. Changes the entire deploy execution model.
- **Wins:** Bullet-proof against any SSH/network issue. Deploy can survive GHA runner death.
- **Costs:** More moving parts; status reporting becomes async; need to handle "deploy still running" edge cases.

### Option D — Concurrency guard (already shipped)

The `concurrency: deploy-production` guard at `deploy.yml:43-45` is already in place from PR #232. **No action needed** for this — confirming it's there. The Documentation_Status entry from 2026-05-11 PM that said "deploy.yml lacks a concurrency: guard" is now stale; the guard was added in PR #233.

## 5. Recommendation

**Ship Option A immediately + Option B as a low-priority follow-up.** Skip Option C unless Option A doesn't resolve the issue within ~5 production deploys.

Rationale:
- Option A is a 1-line change with vanishingly small risk.
- It addresses the actual root cause (SSH idle eviction).
- Option B adds defense-in-depth and operational visibility but is orthogonal to whether A works.
- Option C is a substantial refactor — only justified if A+B prove insufficient.

## 6. Verification plan (post-Option-A)

After shipping Option A, watch the next 3 deploys:

| Check | Expected after fix |
|---|---|
| Deploy workflow exit code | `success` (0) |
| Workflow log between "Setting deployment-window flag" and "Installing canonical VP worker unit template" | Either no gap (because the step is fast) or filled with keepalive-driven empty-but-alive periods (no `Terminated`) |
| `/api/v1/version` SHA on VPS | Matches merged SHA (this should keep working regardless) |
| Time from PR merge to GHA green | Roughly 4-7 minutes (current observed wall-time of the full deploy) |

If 3 consecutive deploys are green and code reaches the VPS, mark the issue closed in `docs/Documentation_Status.md`. If even one false-failure recurs, escalate to Option B.

## 7. Rollback plan

`git revert <fix-commit>` returns the workflow to its current state. The current state is already "broken" (false failures), so worst-case is back to today's baseline.

## 8. Out of scope (will not be addressed by this plan)

- The recurring gateway-incident pattern (PR #289 → #297 chain) — that's separate work tracked in the 2026-05-16 gateway incident memory entry.
- Real deploy failures (e.g. failed `npm install`, broken systemd unit, Python import error at startup). Those will still surface and should not be papered over.
- The 30-minute `timeout 30m` cap on the SSH command at `deploy.yml:97` — that's the correct outer bound; Option A operates inside that envelope.

## 9. Open questions for operator review

1. **Approve Option A as the immediate fix?** ✅ Approved 2026-05-16 PM.
2. **Approve Option B as a follow-up?** ✅ Approved 2026-05-16 PM (shipped together with A in same PR).
3. **Any reason to skip straight to Option C?** ❌ No — defer C unless A+B prove insufficient over ~5 deploys.

---

## 10. Implementation (2026-05-16 PM)

Options A + B shipped together. Code anchors:

| Option | File | Change |
|---|---|---|
| A — SSH keepalive flags | `.github/workflows/deploy.yml` (around line 97) | Added `-o ServerAliveInterval=30 -o ServerAliveCountMax=120` to the deploy `ssh` invocation. Keepalive every 30s, tolerate up to 60 min of pure idle, inside the outer `timeout 30m` envelope. |
| B — heartbeat during silent steps | `scripts/install_vps_systemd_units.sh` | Background `( while true; printf heartbeat; sleep 30; done )` loop started before `systemctl daemon-reload` + `systemctl enable`, killed via `trap EXIT`. |
| B — heartbeat during silent steps | `scripts/install_vp_worker_services.sh` | Same pattern as above, started before `systemctl daemon-reload` + `systemctl enable --now`. |

Each heartbeat emits a line like:

```
[heartbeat install_vps_systemd_units] 2026-05-16T17:45:30+00:00
[heartbeat install_vps_systemd_units] 2026-05-16T17:46:00+00:00
```

Combined effect: Option A keeps the SSH TCP connection alive regardless of remote-side silence (defense at the connection layer); Option B makes remote-side silence shorter and gives operators visible progress in the workflow log (defense at the application layer).

### Verification plan (active)

Watch the next 5 production deploys after this PR merges. Per § 6, success criteria:

- Workflow exit code `success` (0)
- No more than ~2 minutes of true silence between log lines (heartbeats fill any longer gap)
- `/api/v1/version` SHA still matches merged SHA (Rule A — should continue working regardless)

If 5 consecutive green deploys: close this issue, mark the 2026-05-11 PM `Documentation_Status.md` concurrency-caveat entry as fully resolved (concurrency guard was already shipped in PR #233; this addresses the orthogonal SSH-timeout class).

If even one false-failure recurs with the same signature (long silent gap + `Terminated`): escalate to Option C and open a follow-up issue.

---

## Related docs

- [CLAUDE.md "Production Verification Rules"](../../CLAUDE.md) §1–8 — why Rule A SHA-check matters.
- [docs/03_Operations/130_Production_Verification_Rules.md](../03_Operations/130_Production_Verification_Rules.md) — Ship-then-Verify cadence (Rules A–D).
- [docs/deployment/ci_cd_pipeline.md](../deployment/ci_cd_pipeline.md) — canonical pipeline reference.
- [docs/06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md](../06_Deployment_And_Environments/04_Branching_And_Release_Workflow.md) — branch model + auto-merge for `claude/*` PRs.
