# OpenClaw Sync Discoveries

This directory contains automated analysis reports comparing new OpenClaw framework releases against our Universal Agent codebase.

## Purpose

The [OpenClaw agent framework](https://github.com/openclaw/openclaw) is a key source of inspiration for our system. While our architectures differ, tracking their innovations helps us identify features worth adopting, watching, or investigating.

## How It Works

1. **GitHub Actions** runs a biweekly scan (Tue + Fri) of OpenClaw's GitHub Releases
2. **Stage 1** (deterministic Python) detects new releases and produces a structured change report
3. **Stage 2** (VP coder agent) analyzes each change against our codebase and produces adoption recommendations

## Directory Structure

```
Openclaw Sync Discoveries/
├── README.md                    ← this file
├── 2026-03-21/                  ← one directory per scan date
│   ├── SYNC_REPORT.md           ← human-readable analysis
│   └── sync_analysis.json       ← structured machine-readable data
├── 2026-03-25/
│   ├── SYNC_REPORT.md
│   └── sync_analysis.json
└── ...
```

## Report Contents

Each `SYNC_REPORT.md` contains per-feature analysis with:

| Field | Description |
|-------|-------------|
| **Feature** | What OpenClaw added |
| **OpenClaw References** | Files/dirs in OpenClaw to study |
| **Relevance** | HIGH / MEDIUM / LOW / NOT_APPLICABLE |
| **Recommendation** | ADOPT / WATCH / SKIP / INVESTIGATE |
| **Our Counterpart** | Where this would live in our codebase |
| **Gap Analysis** | What we have vs. what this adds |
| **Implementation Notes** | How we'd emulate it |
| **Effort** | T-shirt size (S/M/L/XL) |

## Recurring Innovation Gaps

Features that keep appearing with WATCH/INVESTIGATE status across multiple reports are automatically elevated — signaling we should seriously consider building that capability.

## Configuration

- **Schedule:** Tue + Fri at 10:13 UTC (configurable in `.github/workflows/openclaw-release-sync.yml`)
- **Stage 1 script:** `src/universal_agent/scripts/openclaw_release_scanner.py`
- **Stage 2 script:** `src/universal_agent/scripts/openclaw_sync_agent.py`
- **Raw release reports:** `artifacts/openclaw-sync/<date>/`
