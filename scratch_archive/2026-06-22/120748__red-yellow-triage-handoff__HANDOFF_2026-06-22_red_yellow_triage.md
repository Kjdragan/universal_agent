# HANDOFF — Investigate the red/yellow items from the 2026-06-22 Simone-email triage

**Purpose.** A fresh session should investigate the flagged items below to determine whether each is a *real* issue, localize root cause (read-only first), and propose/triage fixes. The operator will decide sequential vs parallel — a recommendation is in §4.
**Prepared:** 2026-06-22 ~11:43 AM CDT (16:43 UTC). **Live deployed SHA:** `35d12517`. **Box:** `ua@uaonvss` → `ssh ua@uaonvps` (runtime user `ua`, passwordless sudo, `/opt/universal_agent`). **All timestamps CDT unless marked UTC** (CDT = UTC−5).

These came from triaging the 12 emails Simone (`oddcity216@agentmail.to`) sent to Kevin's Gmail since 7 AM CDT today. Four warranted investigation: **#2 OOM, #3/#11 sweeper restarts, #12 disk‑95%** (a likely-linked resource cluster) and **#4 VP runtime cap** (independent design issue).

---

## 0. Current live state (read-only snapshot, 16:43 UTC) — start here, don't re-derive

- **RAM:** 15 GiB total · **11 GiB used · 294 MiB free · 4.2 GiB available**.
- **Top consumer by far:** `ollama` **`llama-server`** (PID was 1197508) at **~9.3 GB RSS** — a multimodal model (`--mmproj …`, ctx 4096, port 127.0.0.1:35043, model blob under `/root/.ollama/models/`). Everything else (python/claude/node) is <0.35 GB each.
- **OOM events today: 3.** The 12:08:03 UTC one: `dockerd invoked oom-killer … global_oom … task=llama-server` → `Killed process 1066709 (llama-server) total-vm:9.6GB anon-rss:6.1GB`. It respawned and is now *larger*.
- **Disk:** `/` (single `/dev/sda1`, 193 GB) now **88% (169 G used, 25 G avail)** — **eased from 95%** in the 11:20 alert after the deploy's `uv cache prune`. uv caches total only **~1.2 GB** (603M ~/.cache/uv + 141M /root/.cache/uv + 440M /tmp/uv_cache + 1.5M repo) → **uv is NOT the 169 GB driver**; the real bulk is unaccounted (suspect `/root/.ollama` model blobs + `/var/lib/docker`).
- **mission-control-sweeper:** `ActiveState=active, running, NRestarts=0` (systemd's own counter), last start **16:02:43 UTC**. `NRestarts=0` means *systemd* didn't restart it — the external **service-watchdog** did (the two alert emails). So it IS flapping, restarted by the watchdog. ⚠️ Note: a naïve `journalctl | grep -c mission-control-sweeper` returns ~2082 — that's **log-line count, NOT restart count**; do not quote it as restarts.

---

## 1. The flagged items (evidence + leading hypothesis)

### #2 — `[ERROR] VPS OOM Kill Detected` (7:09 AM / 12:09 UTC) 🔴
- **Evidence:** kernel `global_oom`, `task=llama-server`, killed under the `docker.service` cgroup. 3 OOMs today; live `llama-server` RSS ~9.3 GB on a 15 GB box.
- **Hypothesis:** ollama keeps a ~6–9 GB multimodal model **resident**, leaving <1 GB headroom → any spike triggers a **global** OOM (kernel picks a victim; today it was ollama itself, but a global OOM can kill *any* process incl. the gateway/sweeper).
- **Key unknowns to resolve:** (a) Is ollama a **UA dependency** (local vision/embeddings backend) or a co-tenant? (b) Can it be memory-bounded (`OLLAMA_KEEP_ALIVE`, `OLLAMA_MAX_LOADED_MODELS=1`, smaller/quantized model, on-demand load) or moved off-box? (c) What actually invokes `127.0.0.1:35043`?

### #3 / #11 — `[ALERT ROLLUP] Watchdog restarted universal-agent-mission-control-sweeper` (7:14 AM & 11:05 AM) 🔴
- **Evidence:** rollup emails ("×1 additional"), so ≥2 restart windows today; sweeper currently up since 16:02 UTC, restarted by the **service-watchdog** (not systemd).
- **Hypothesis:** the sweeper is deactivating ("inactive:deactivating; post-state: active" was the Sunday pattern for a different unit) — either crashing, exiting cleanly when it shouldn't, or being **OOM-killed during the memory pressure above** (strong candidate — check time correlation with the 3 OOM events).
- **Service:** `services/mission_control_sweeper_main.py` (the extracted standalone sweeper).

### #12 — `[ACTION/INCIDENT] Proactive Health: disk_usage_health CRITICAL — 95%` (11:20 AM / 16:20 UTC) 🔴
- **Evidence:** `/`, `/opt`, `/var/lib` all 95% (same `/dev/sda1`), 9.6 GB free at alert time; now 88% / 25 GB free after the deploy prune. The invariant self-diagnosed uv cache as "top reclaimable," but uv is only ~1.2 GB — the **169 GB used is mostly elsewhere and unexplained**.
- **Hypothesis:** the durable consumers are ollama model blobs (`/root/.ollama/models`) and/or docker (`/var/lib/docker` images+volumes) and/or DB/journal growth — not the auto-pruned uv cache. The 95%→88% dip is a temporary deploy artifact; it'll climb back.
- **Invariant:** `services/invariants/csi_source_liveness.py`'s sibling disk invariant (the email's `metric_key=disk_usage_health`).

### #4 — `[VP Status] paper_to_podcast Cody mission "failed" but work shipped` (7:20 AM) 🟡 — design decision, independent
- **Evidence:** Simone verified the failure was **cosmetic** — Cody ran **1812 s against a 1500 s cap** and was killed at the runtime ceiling *right as PR #1143's squash-merge finished*, before writing its completion attestation. The fix is shipped + deployed + verified.
- **The real ask (Simone's, needs your nod):** the **1500 s mission cap is shorter than CI+merge latency for code missions**, so *any* mission that merges near the cap false-fails like this. Options: (a) raise `max_runtime` for `code_generation`/Cody missions; (b) treat "PR merged during the window" as a success signal. This is a **policy/code decision**, not a break.

---

## 2. Likely linkage

**#2, #3, #12 are plausibly one story: resource exhaustion on a small box.** A 15 GB-RAM / 193 GB-disk host runs a ~9 GB resident ollama model → recurring global OOM (#2); a global OOM and/or memory starvation is a strong candidate for the sweeper flapping (#3); and disk is independently tight (#12, likely ollama blobs + docker). Investigate them **together** so the diagnosis is coherent (e.g., bounding ollama memory may fix both #2 and #3; accounting disk may implicate the same ollama footprint).

**#4 is independent** — a VP-runtime-policy decision with no infra coupling.

---

## 3. Investigation plan (read-only first; nothing destructive without operator OK)

### Track A — VPS resource pressure (#2 + #3 + #12)
Read-only probes:
- **Ollama's role in UA:** `grep -rni "ollama\|35043\|11434" /opt/universal_agent/src /opt/universal_agent/CSI_Ingester 2>/dev/null | head`; identify the caller (vision? embeddings? a specific lane). Check `systemctl cat ollama* 2>/dev/null` and `ls -lah /root/.ollama/models/blobs`.
- **OOM history + victims:** `sudo journalctl -k --since "3 days ago" | grep -iE "oom-kill|Killed process"` — frequency, which processes, whether the gateway/sweeper were ever victims.
- **Disk accounting (the real 169 GB):** `sudo du -xh --max-depth=1 / 2>/dev/null | sort -rh | head -15`; then drill `sudo du -sh /root/.ollama /var/lib/docker /opt/universal_agent /var/lib/universal-agent /var/log 2>/dev/null`; `journalctl --disk-usage`; `docker system df` (if docker is ours).
- **Sweeper exit reason + correlation:** `sudo journalctl -u universal-agent-mission-control-sweeper.service --since "today" --no-pager | tail -80` — look for tracebacks / SIGKILL / clean-exit; cross-reference timestamps against the 3 OOM events and the watchdog restart emails (7:14, 11:05 CDT).
Candidate fixes (propose, don't apply without OK): bound ollama (`OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_KEEP_ALIVE` short, smaller/quantized model, or `MemoryMax=`/`MemoryHigh=` on its unit); prune the true disk consumer; if the sweeper is OOM-collateral, the memory fix likely resolves it.

### Track B — VP runtime cap (#4)
- Locate the cap: `grep -rni "1500\|max_runtime\|mission.*timeout" /opt/universal_agent/src/universal_agent/vp/ /opt/universal_agent/src/universal_agent/services/timeout_policy.py 2>/dev/null` (and any `UA_VP_*` / `code_generation` runtime knob). Confirm where code missions get the 1500 s ceiling vs the LivenessWatchdog policy.
- Decide between Simone's two options (raise the code-mission cap, or count "PR merged in-window" as success). Implement the chosen one behind the normal branch→PR→auto-merge flow.

---

## 4. Recommendation — sequential vs parallel

**Two parallel tracks:**
- **Track A (resource pressure: #2/#3/#12)** — run as **one investigation** (the items are coupled; a single agent or a small parallel fan-out feeding one diagnosis). Highest urgency: a global OOM can kill the gateway, and disk will re-climb.
- **Track B (VP cap: #4)** — **independent, can run in parallel** with A. Lower urgency (cosmetic false-fail; fix already shipped). It ends in a one-line policy decision + small PR.

So: launch A and B in parallel; within A, investigate #2/#3/#12 together rather than as three separate threads.

---

## 5. Guardrails
- This is the **production VPS**. Read-only first; `ua` has passwordless sudo but **do not restart/kill/`docker`-prune/delete or change ollama, docker, or any unit without operator approval** — unloading the model or pruning the wrong thing could disrupt a live consumer.
- The disk 95%→88% dip is a deploy artifact, not a fix — don't mark #12 resolved on that alone.
- Verify any "is it live/dead?" question against the canonical resolver, not a guess (see `project_docs/00_PLATFORM_STATUS_REGISTRY.md`).
- Source emails (for full text): Gmail `from:oddcity216@agentmail.to after:2026/06/22`. Full triage list is in the chat that produced this handoff.
