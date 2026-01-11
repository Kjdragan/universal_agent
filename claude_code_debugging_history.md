# Claude Code + Z.AI Latency Debugging Summary

## Purpose of This Document
This document captures all investigation, evidence, and conclusions from a multi‑hour debugging session diagnosing **very long pre‑token latency** ("Spinning… 0 tokens") when running **Claude Code v2.1.3** against **Z.AI’s Anthropic‑compatible API** from **WSL2 on Windows 10**.

The intent is to allow a **fresh conversation** to resume debugging *without repeating prior work*, preserving what has been ruled out, what has been proven, and what remains to be tested.

---

## Environment Overview

- **Host OS:** Windows 10 Pro
- **Subsystem:** WSL2 (Ubuntu 24.04 / Noble)
- **CPU / RAM:** Healthy, low load, ample free memory
- **Networking:** Gigabit Ethernet
- **Claude Code:** v2.1.3 (native install)
- **Claude Base URL:** `https://api.z.ai/api/anthropic`
- **Model:** `claude-sonnet-4-5-20250929` (Z.AI → GLM‑4.7 backend)

Claude Code is invoked from **Linux home directories**, not Windows mounts.

---

## Core Symptom

In Claude Code interactive sessions:

- Prompts like `hello` show:
  - **“Spinning… ↑ 0 tokens”** for **30–55 seconds**
  - Then inference completes quickly once tokens start

Key observation:
> **The delay occurs entirely before the first streamed token is received.**

This is *not* slow inference — it is pre‑first‑token latency.

---

## What Was Proven (Hard Evidence)

### 1. Z.AI API Network + Streaming Are Fast

Direct curl tests from WSL to Z.AI:

- **Non‑streaming request**
  - TTFB ≈ **1.1s**
  - Total ≈ **1.1s**
- **Streaming request**
  - `message_start`, `content_block_delta`, tokens arrive immediately

Conclusion:
> Z.AI endpoint can accept requests and begin streaming promptly from this machine.

This rules out:
- IPv6 routing issues (for the API itself)
- General packet loss
- Network latency
- TLS handshake delays

---

### 2. Problem Persists in a Clean Directory

Tests were run in:
- `~/claude_empty` (no git repo, no files)

Slowness still occurred.

Conclusion:
> Repository scanning / git hooks are **not** the root cause.

---

### 3. Problem Persists with Isolated Claude Config

Claude Code was run with fresh XDG dirs:

```bash
XDG_CONFIG_HOME=~/claude_isolation/config \
XDG_CACHE_HOME=~/claude_isolation/cache \
XDG_STATE_HOME=~/claude_isolation/state \
claude -p "hello"
```

Result:
- **~21 seconds** real time

Conclusion:
> This is **not contamination from ~/.claude config**, memory, or prior projects.

---

### 4. MCP Servers Significantly Contribute — but Are Not the Only Cause

Initial MCP setup included:

- `zai-mcp-server` (local Node process via `npx -y @z_ai/mcp-server`)
- `web-search-prime` (HTTP MCP)
- `web-reader` (HTTP MCP)
- `zread` (HTTP MCP)

Findings:

- Removing **only** `zai-mcp-server` → **no major change**
- Removing **all MCP servers** → latency dropped from ~55s to **8–18s**

Conclusion:
> MCP initialization adds **significant overhead**, but **does not fully explain the delay**.

---

### 5. MCP Is Not Required for Basic Z.AI Inference

Claude Code can:
- Send requests
- Receive streaming tokens

…without any MCP servers present.

---

### 6. System Health Is Normal

- `/proc/loadavg` near zero
- Plenty of free RAM
- No swap usage
- No background STT/TTS/audio processes in WSL

Conclusion:
> Not CPU, RAM, or general system contention.

---

## Critical strace Findings

`strace -c -f` results show:

- **~90% of total runtime spent in `futex()`**
- Secondary time in `wait4()`, `epoll_pwait2()`
- Very little time in `connect()`, `recvfrom()`, or filesystem calls

Interpretation:
> Claude Code is **waiting on internal threads or child processes**, not doing I/O.

This aligns with:
- Tool orchestration
- Worker thread initialization
- Synchronous preflight steps before request dispatch

---

## What Is NOT the Cause

Ruled out with evidence:

- ❌ Network latency
- ❌ IPv6 connectivity to Z.AI API
- ❌ Z.AI streaming implementation
- ❌ Windows filesystem mounts
- ❌ Git repo scanning
- ❌ Claude Agent SDK project hooks bleeding into Claude Code
- ❌ User ~/.claude config corruption
- ❌ CPU / memory pressure

---

## Most Likely Root Causes (Current Hypothesis)

One or more of the following **inside Claude Code itself**:

1. **Synchronous preflight orchestration before first token**
   - Tool registry initialization
   - Internal worker pool startup
   - Health checks even when tools are unused

2. **Blocking waits on child processes / threads**
   - Matches futex‑dominated strace

3. **Claude Code runtime behavior specific to WSL**
   - Interaction between CLI runtime + WSL scheduling

4. **Non‑MCP internal services**
   - Search index
   - Session store
   - Telemetry scaffolding (even when disabled)

---

## What Remains To Be Done (Next Session Plan)

### 1. Timestamped strace to identify exact stall

Run:
```bash
sudo strace -f -tt -T -o /tmp/claude_t.log claude -p "hello"
```

Then extract longest waits:
```bash
grep -E '<[0-9]+\.[0-9]+>' /tmp/claude_t.log | sort -t'<' -k2,2nr | head -n 30
```

Goal:
- Identify **which syscall(s)** block for tens of seconds
- Determine whether it’s `wait4`, `futex`, or something else

---

### 2. Confirm MCP list is empty

```bash
claude mcp list
```

Ensure no servers are active during testing.

---

### 3. Disk I/O sanity check (WSL ↔ Windows interaction)

```bash
dd if=/dev/zero of=/tmp/io_test.bin bs=1M count=512 conv=fdatasync
rm /tmp/io_test.bin
```

Goal:
- Rule out Defender / VHDX latency causing blocking waits

---

### 4. (If needed) Compare with native Linux or Windows install

If possible:
- Run Claude Code on:
  - Native Linux (non‑WSL)
  - Or Windows host

Compare pre‑token latency.

---

## Key Takeaway

> **Claude Code is slow before first token because of internal orchestration and waiting — not because of the Z.AI API or your network.**

This document preserves the full state of investigation so debugging can resume immediately in a new conversation.

---

**End of summary**

