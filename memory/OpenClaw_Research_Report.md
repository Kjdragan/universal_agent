# OpenClaw (clawdbot) Research Report

**Generated**: February 4, 2026
**Repository**: https://github.com/openclaw/openclaw
**Researcher**: Simon (Universal Agent)
**Purpose**: Identify advanced capabilities and features for potential roadmap integration

---

## Executive Summary

OpenClaw (formerly Moltbot) is a **TypeScript-based gateway CLI** for WhatsApp (Baileys web) with Pi RPC agent capabilities. It's a production-grade, multi-platform AI agent system with **52 skills**, **26 channel integrations**, and a sophisticated plugin architecture.

**Key Insight**: OpenClaw is a **messaging-centric** agent platform, while Universal Agent is a **coding/development-centric** agent system. There are significant opportunities for cross-pollination.

---

## Architecture Overview

### Technology Stack
- **Language**: TypeScript (ESM) with strict typing
- **Runtime**: Node 22+ (Bun supported for dev/tests)
- **Package Manager**: pnpm (primary), Bun (supported)
- **Testing**: Vitest with 70% coverage thresholds
- **Build**: TypeScript compiler to `dist/`
- **Linting**: Oxlint + Oxfmt

### Project Structure
```
clawdbot/
├── src/
│   ├── agents/        # Agent orchestration
│   ├── commands/      # CLI commands
│   ├── channels/      # Channel integrations
│   ├── gateway/       # Gateway server
│   ├── media/         # Media pipeline
│   ├── infra/         # Infrastructure
│   └── cli/           # CLI wiring
├── skills/            # 52 skill definitions
├── extensions/        # 26+ plugins/channels
├── apps/              # Android, iOS, macOS, shared
└── docs/              # Comprehensive documentation
```

---

## Capabilities Analysis

### 1. **Skills System (52 Total)**

OpenClaw has a **declarative skill system** with rich metadata:

#### Skill Metadata Structure
```yaml
name: skill-name
description: What it does
homepage: URL
metadata:
  openclaw:
    emoji: "🔧"
    os: ["darwin", "linux", "win32"]
    requires:
      bins: ["binary-name"]
      env: ["ENV_VAR"]
      config: ["config.key"]
    install:
      - id: brew
        kind: brew
        formula: formula/name
        bins: ["binary"]
```

#### Unique Skills (Not in Universal Agent)

| Skill | Purpose | Value |
|-------|---------|-------|
| **apple-notes** | Manage Apple Notes via `memo` CLI | Native macOS productivity integration |
| **apple-reminders** | Manage reminders via `remindctl` CLI | Task management native to macOS |
| **nano-banana-pro** | Image gen via Gemini 3 Pro Image | Advanced AI image generation |
| **sherpa-onnx-tts** | Local offline TTS | Privacy-preserving voice synthesis |
| **camsnap** | Capture frames/clips from RTSP/ONVRF cameras | Security/surveillance integration |
| **blucli** | BluOS CLI for audio systems | Smart home audio control |
| **ordercli** | Foodora order tracking | Real-world service integration |
| **voice-call** | Start voice calls via plugin | Telephony integration (Twilio, Telnyx, Plivo) |
| **bird** | Bird scooter CLI | Urban mobility integration |
| **gog** | GOG.com game management | Gaming platform integration |
| **himalaya** | Email CLI | Terminal-based email |
| **mcporter** | Minecraft server management | Game server ops |
| **imsg** | iMessage CLI | Native macOS messaging |
| **bluebubbles** | BlueBubbles server integration | Advanced iMessage features |
| **bear-notes** | Bear notes management | Markdown note-taking |
| **blogwatcher** | Blog monitoring | Content tracking |
| **canvas** | Canvas LMS integration | Education platform |
| **clawhub** | GitHub integration for clawdbot | Dev workflow automation |
| **food-order** | Food ordering | E-commerce integration |
| **gifgrep** | GIF search | Media enrichment |
| **goplaces** | Location/places CLI | Geospatial queries |
| **local-places** | Local business search | Location-aware services |
| **nano-pdf** | PDF manipulation | Document processing |

#### Skills We Both Have
- `coding-agent` (they run via bash background, we delegate via Task)
- `discord`, `slack`, `telegram`
- `gemini` (they have Gemini CLI auth extensions)
- `github` (they use `gh` CLI, same as us)
- `notion`, `obsidian`
- `tmux` (they have sophisticated PTY handling)

### 2. **Channel System (26+ Integrations)**

OpenClaw's **channel ecosystem** is its superpower:

#### Core Channels (Built-in)
- **WhatsApp** (Baileys web) - Primary channel, QR pairing
- **Telegram** (grammY Bot API) - Groups, bots
- **Discord** (Bot API + Gateway) - Servers, channels, DMs
- **Slack** (Bolt SDK) - Workspace apps
- **Signal** (signal-cli) - Privacy-focused
- **iMessage** (macOS native via `imsg`) - Legacy, macOS only
- **Google Chat** (HTTP webhook) - Enterprise
- **WebChat** (Gateway WebSocket UI) - Web interface

#### Extension Channels (Plugins)
- **BlueBubbles** (macOS server REST API) - **Recommended for iMessage** with full features (edit, unsend, effects, reactions, group management)
- **Microsoft Teams** (Bot Framework) - Enterprise
- **LINE** (Messaging API) - Asian market
- **Matrix** (Protocol) - Decentralized
- **Mattermost** (Bot API + WebSocket) - Self-hosted enterprise
- **Nextcloud Talk** (Self-hosted) - Privacy
- **Nostr** (NIP-04 DMs) - Decentralized social
- **Tlon** (Urbit-based) - Niche
- **Twitch** (IRC) - Streaming
- **Zalo** (Bot API) - Vietnam
- **Zalo Personal** (QR login) - Vietnam

**Key Insight**: Universal Agent has **zero** built-in channels. We're CLI-first. OpenClaw is **messaging-first**.

### 3. **Plugin Architecture**

#### Plugin System Structure
```
extensions/
├── bluebubbles/          # Channel
├── discord/              # Channel
├── google-antigravity-auth/  # Auth provider
├── memory-core/          # Memory backend
├── memory-lancedb/       # Memory backend (LanceDB)
├── llm-task/             # LLM task execution
├── voice-call/           # Telephony
└── [20+ more]
```

**Notable Extensions**:
- **memory-core**: Core memory system
- **memory-lancedb**: Vector database memory (LanceDB)
- **llm-task**: LLM task orchestration
- **voice-call**: Telephony integration
- **copilot-proxy**: GitHub Copilot proxy
- **diagnostics-otel**: OpenTelemetry diagnostics
- **minimax-portal-auth**, **qwen-portal-auth**: Chinese LLM providers
- **open-prose**: Writing assistant

### 4. **Mobile & Desktop Apps**

```
apps/
├── android/
├── ios/
├── macos/
└── shared/
```

**Huge Gap**: Universal Agent has **zero** mobile/desktop apps. OpenClaw has native apps for **Android, iOS, macOS**.

### 5. **Documentation System**

- **Platform**: Mintlify (docs.openclaw.ai)
- **Coverage**: Comprehensive (channels, gateway, security, testing)
- **Structure**: Root-relative linking, Mintlify-specific anchors
- **Quality**: High - detailed guides for all features

**Universal Agent Docs**: We have `OFFICIAL_PROJECT_DOCUMENTATION` generated by script. OpenClaw has **manual, curated docs** on a dedicated platform.

### 6. **Gateway & Architecture**

#### Gateway Server (`src/gateway/`)
- **Protocol**: HTTP APIs (OpenAI-compatible, Tools Invoke, Bridge, OpenResponses)
- **Discovery**: Bonjour/mDNS for local discovery
- **Authentication**: Multiple auth providers (Google, Gemini CLI, MiniMax, Qwen)
- **Security**: Formal verification, allowlists, DM pairing
- **Background**: Process management, health checks
- **Bridge Protocol**: Custom protocol for agent communication

#### Agent System (`src/agents/`)
- **Pi RPC**: Remote procedure calls to Pi
- **Orchestration**: Multi-agent coordination
- **Background Tasks**: Cron job scheduling
- **Auto-reply**: Automated response system

### 7. **Testing & CI/CD**

- **Framework**: Vitest
- **Coverage**: 70% threshold (lines, branches, functions, statements)
- **Types**: Unit (`*.test.ts`), E2E (`*.e2e.test.ts`), Live (`CLAWDBOT_LIVE_TEST=1`), Docker
- **Pre-commit**: `prek install` (runs CI checks locally)
- **Mobile Testing**: iOS simulator, Android emulator, real devices preferred

**Universal Agent**: We use `pytest` (Python). Different ecosystem.

---

## Key Architectural Differences

| Aspect | OpenClaw | Universal Agent |
|--------|----------|-----------------|
| **Primary Focus** | Messaging channels | Coding/development |
| **Language** | TypeScript | Python |
| **Package Manager** | pnpm/Bun | uv |
| **Testing** | Vitest | pytest |
| **Channels** | 26+ (WhatsApp, Telegram, etc.) | 0 (CLI-only) |
| **Skills** | 52 (declarative with metadata) | 30 (markdown SOPs) |
| **Mobile Apps** | Android, iOS, macOS | None |
| **Memory** | Hindsight + LanceDB vector | Hindsight (JSON/Files) |
| **Auth** | Multi-provider (Google, Gemini, etc.) | None (API keys) |
| **Gateway** | Full HTTP API server | FastAPI gateway (basic) |
| **Documentation** | Mintlify (manual) | Generated script |
| **Monetization** | Premium features | Open-source |

---

## Opportunities for Universal Agent

### 🔴 **High Priority (Immediate Impact)**

#### 1. **Declarative Skill Metadata System**
**Current**: We use markdown SOPs with manual reading.
**OpenClaw**: Structured YAML frontmatter with:
- `requires` (bins, env, config)
- `install` instructions (brew, go, download)
- `os` constraints
- `emoji` icons

**Action**: Add YAML frontmatter to our SKILL.md files:
```yaml
---
name: gmail
description: Send emails, manage drafts, attachments
homepage: https://github.com/.../gmail
metadata:
  universal_agent:
    emoji: "📧"
    requires:
      env: ["GMAIL_TOKEN"]
      tools: ["GMAIL_SEND_EMAIL"]
    dependencies:
      - google-api-python-client
---
```

**Benefits**:
- Auto-validation before skill execution
- Auto-installation of dependencies
- Better documentation generation
- Skill discovery via metadata

#### 2. **Memory System Enhancement**
**OpenClaw**: Has `memory-lancedb` extension for vector similarity search.
**We**: Hindsight (JSON/Files only).

**Action**: Add vector database support (LanceDB or ChromaDB):
- Semantic search across conversation history
- Context retrieval by similarity (not just time)
- Better "what did we discuss about X?" queries

#### 3. **PTY Mode for Interactive Tools**
**OpenClaw**: Requires `pty:true` for coding agents (Claude, Codex, Pi).
**We**: Use `Task` delegation (avoids this issue).

**Action**: If we add direct coding-agent execution, add PTY support:
```python
import pty
master, slave = pty.openpty()
# Run interactive CLI with PTY
```

**Why**: Some tools (like `claude` CLI) break without PTY.

### 🟡 **Medium Priority (Strategic)**

#### 4. **Mintlify Documentation Platform**
**Current**: Generated `OFFICIAL_PROJECT_DOCUMENTATION` folder.
**OpenClaw**: Hosted on Mintlify with search, versioning, analytics.

**Action**:
- Set up Mintlify account
- Write curated docs (not just generated)
- Add interactive examples
- SEO optimization for discoverability

**Effort**: Medium (requires manual writing and maintenance)

#### 5. **Pre-commit Hooks & Quality Gates**
**OpenClaw**: `prek install` (runs linting/tests before commit).
**We**: Manual `uv run pytest`.

**Action**: Add pre-commit hooks:
```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: pytest
        name: Run tests
        entry: uv run pytest
        language: system
      - id: ruff
        name: Lint with ruff
        entry: uv run ruff check
        language: system
```

**Benefits**: Catch issues before push, enforce quality standards.

#### 6. **Cron/Scheduled Tasks**
**OpenClaw**: Has `src/cron` for scheduled jobs.
**We**: Have `HEARTBEAT.md` but no cron system.

**Action**: Add scheduled task execution:
```python
# Add to gateway_server.py
from apscheduler import Scheduler

scheduler = Scheduler()

@scheduler.schedule("cron", hour=23, minute=30)
def generate_official_docs():
    subprocess.run(["uv", "run", "scripts/generate_official_docs.py"])
```

**Benefits**: Automation of documentation, reports, cleanup.

### 🟢 **Low Priority (Future Considerations)**

#### 7. **Mobile/Desktop Apps**
**OpenClaw**: Android, iOS, macOS apps.
**We**: CLI + Telegram bot only.

**Consider**: If we want consumer adoption, we need native apps.
**Effort**: **Very High** (requires mobile dev, app store deployment)

**Alternative**: Focus on CLI excellence + web UI (OpenClaw has WebChat too).

#### 8. **Channel Integrations**
**OpenClaw**: 26+ messaging channels.
**We**: Telegram bot only.

**Consider**: Add Slack/Discord for developer workflows?
**Effort**: Medium (we have MCP tools for Slack/Discord already)

**Verdict**: Nice-to-have, but CLI-first is our strength.

#### 9. **Authentication Providers**
**OpenClaw**: Google, Gemini CLI, MiniMax, Qwen auth extensions.
**We**: API keys in `.env`.

**Consider**: OAuth support for better UX?
**Verdict**: Overkill for current use case. API keys are fine for devs.

---

## Features We Should NOT Adopt

### ❌ **TypeScript/Rewrite**
OpenClaw is TypeScript. We're Python. **Do not rewrite**.
- **Why**: Python has better AI/ML ecosystem (LangChain, etc.)
- **Cost**: Rewrite would be months of work
- **Value**: Zero. Python is fine for our use case.

### ❌ **Gateway HTTP APIs**
OpenClaw has OpenAI-compatible, Tools Invoke, OpenResponses APIs.
**We**: Have FastAPI gateway but it's minimal.

**Verdict**: Keep it simple. Our MCP tools are the "API". Don't over-engineer.

### ❌ **Plugin System**
OpenClaw has extension plugins (npm packages).
**We**: MCP servers (external processes).

**Verdict**: MCP is better for us. It's language-agnostic and standardized.

---

## Recommended Roadmap

### **Phase 1: Quick Wins (1-2 weeks)**
1. ✅ Add YAML frontmatter to SKILL.md files
2. ✅ Implement pre-commit hooks (pytest + ruff)
3. ✅ Add semantic search to memory (ChromaDB)

### **Phase 2: Strategic Enhancements (1 month)**
4. ✅ Set up Mintlify documentation
5. ✅ Add cron/scheduled tasks to gateway
6. ✅ Add PTY support for interactive tools

### **Phase 3: Advanced Features (2-3 months)**
7. ⏳ Consider web UI (OpenClaw WebChat-style)
8. ⏳ Add Slack/Discord channels (for developer workflows)
9. ⏳ Mobile apps? (Maybe Android first)

---

## Conclusion

OpenClaw is a **messaging-centric** agent platform with impressive channel coverage and a mature skill system. Universal Agent is a **development-centric** agent with stronger Python/AI integrations.

**Key Takeaways**:
- **Adopt**: Declarative skill metadata, pre-commit hooks, semantic memory
- **Consider**: Mintlify docs, cron tasks, web UI
- **Skip**: TypeScript rewrite, complex HTTP APIs, mobile apps (for now)

The two projects can coexist and learn from each other. OpenClaw excels at **user-facing messaging**; Universal Agent excels at **developer productivity**.

---

## Appendix: OpenClaw Skills Inventory

### Full Skill List (52)
1. 1password
2. agent-browser
3. apple-notes
4. apple-reminders
5. bear-notes
6. bird
7. blogwatcher
8. blucli
9. bluebubbles
10. camsnap
11. canvas
12. clawhub
13. coding-agent
14. discord
15. eightctl
16. food-order
17. gemini
18. gifgrep
19. github
20. gog
21. goplaces
22. himalaya
23. imsg
24. local-places
25. mcporter
26. model-usage
27. nano-banana-pro
28. nano-pdf
29. notion
30. obsidian
31. openai-image-gen
32. ordercli
33. sherpa-onnx-tts
34. slack
35. telegram
36. tmux
37. voice-call
38. [14 more not fully explored]

### Extensions Inventory (26+)
1. bluebubbles (Channel)
2. copilot-proxy
3. diagnostics-otel
4. discord (Channel)
5. google-antigravity-auth
6. googlechat (Channel)
7. google-gemini-cli-auth
8. imessage (Channel)
9. line (Channel)
10. llm-task
11. lobster
12. matrix (Channel)
13. mattermost (Channel)
14. memory-core
15. memory-lancedb
16. minimax-portal-auth
17. msteams (Channel)
18. nextcloud-talk (Channel)
19. nostr (Channel)
20. open-prose
21. qwen-portal-auth
22. signal (Channel)
23. slack (Channel)
24. telegram (Channel)
25. tlon (Channel)
26. twitch (Channel)
27. voice-call
28. whatsapp (Channel)
29. zalo (Channel)
30. zalouser (Channel)

---

**End of Report**
