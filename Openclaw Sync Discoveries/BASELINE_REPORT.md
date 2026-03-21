# OpenClaw Baseline Report — Feb 21 to Mar 14, 2026

> **Generated:** 2026-03-20  
> **Releases covered:** 13 stable + 7 beta (v2026.2.21 → v2026.3.13-1)  
> **Total feature/change items:** 193  
> **Source data:** `artifacts/openclaw-sync/2026-03-21/release_report.json`

This document catalogs every new feature and significant change in OpenClaw over the last 3+ weeks, organized by **relevance to our Universal Agent system**. Platform-specific items (iOS, macOS, Android, Windows native clients) are grouped in an appendix since our system is server-side only.

---

## 🔴 HIGH RELEVANCE — Agent Runtime & Execution

These directly map to our agent execution engine and should get first attention.

### Compaction & Context Management
- **Persona-preserving compaction** — configurable custom instructions so persona drift is reduced after auto-compaction. (`#10456`)
- **Full-session token count for post-compaction sanity** — compares against pre-compaction totals and skips check when estimation fails. (`#28347`)
- **Skip double-compaction** — skip post-compaction cache-ttl marker write when compaction already completed in same attempt. (`#28548`)
- **Post-compaction memory reindexing** — `agents.defaults.compaction.postIndexSync` for immediate session memory refresh. (`#25561`)
- **Compaction retry bounding** — drain embedded runs during SIGUSR1 restart so session lanes recover. (`#40324`)
- **Context-engine overflow** — guard thrown engine-owned overflow compaction attempts and fire hooks for `ownsCompaction` engines. (`#41361`)
- **Context pruning** — prune image-only tool results during soft-trim and extend historical image cleanup. (`#43045`)

### Subagent & Multi-Agent
- **`sessions_yield`** — end the current turn immediately, skip queued tool work, and carry a hidden follow-up payload into the next turn. (`#36537`)
- **Cross-agent workspace resolution** — resolve target workspace for cross-agent subagent spawns. (`#40176`)
- **Leaf vs orchestrator scoping** — persist leaf/orchestrator control scope at spawn time, prevent leaf sessions from regaining orchestration privileges. 
- **Completion announce retries** — 90s timeout, no retry on gateway-timeout to prevent duplicate completion messages. (`#41235`)
- **Sandbox workspace inheritance** — pass real workspace through `sessions_spawn` inheritance instead of sandbox copy. (`#40757`)
- **Cooldown probing cap** — cap cooldown-bypass probing to one attempt per provider per fallback run. (`#41711`)

### Model Failover & Provider Handling
- **Fast mode toggles** — session-level `/fast` toggle for OpenAI GPT-5.4 and Anthropic Claude with per-model config defaults.
- **HTTP 499 as transient** — Anthropic-style client-closed overload triggers model fallback. (`#41468`)
- **Gemini MALFORMED_RESPONSE** — treat as retryable timeout for preview-model drift. (`#42292`)
- **Venice/Poe billing fallback** — recognize 402 billing errors to trigger model fallback. (`#43205`, `#42278`)
- **Billing recovery probing** — probe single-provider cooldowns on existing throttle for credit top-up recovery. (`#41422`)
- **Structured fallover observability** — sanitized model-fallback decision events with correlated run IDs. (`#41337`)

### Memory & Search
- **Multimodal memory indexing** — opt-in image and audio indexing with Gemini `gemini-embedding-2-preview`. (`#43460`)
- **Configurable embedding dimensions** — Gemini embedding support with auto-reindexing on dimension change. (`#42501`)
- **Memory bootstrap dedup** — load only one root memory file (`MEMORY.md` preferred, `memory.md` fallback). (`#26054`)
- **Memory flush write guard** — forward `memoryFlushWritePath` through embedded agent runs. (`#41761`)

### Agent Text & Error Quality  
- **Strip leaked model control tokens** — removes `<|...|>` and full-width variants from user-facing text (GLM-5, DeepSeek). (`#42173`)
- **Stale error rendering** — ignore stale assistant `errorMessage` fields on successful turns. (`#40616`)
- **Ollama reasoning visibility** — stop promoting native `thinking` fields into final assistant text. (`#45330`)

---

## 🟠 MEDIUM RELEVANCE — Gateway & Infrastructure

### Gateway Session Management
- **Session reset state preservation** — preserve `lastAccountId` and `lastThreadId` across resets. (`#44773`)
- **Session discovery under custom stores** — discover disk-only and retired ACP session stores under templated roots. (`#44176`)
- **Session reset auth split** — separate `/new` and `/reset` from admin-only `sessions.reset` RPC.
- **Config validation surfacing** — show up to three validation issues in config set/patch/apply errors. (`#42664`)
- **Auth fail-closed** — fail when local SecretRefs are configured but unavailable instead of falling back. (`#42672`)
- **Bound unanswered client requests** — prevent unbounded client request accumulation. (`#45689`)
- **Status `--require-rpc`** — allow automation to fail hard on probe misses. 

### Cron & Scheduling
- **Isolated cron delivery tightening** (BREAKING) — no more ad hoc agent sends or fallback main-session summaries from cron jobs. (`#40998`)
- **Nested lane deadlock prevention** — route nested cron work onto nested lane. (`#45459`)
- **Proactive delivery dedup** — keep isolated cron sends out of write-ahead resend queue. (`#40646`)
- **Cron state errors** — record `lastErrorReason` in cron job state. (`#14382`)

### Hook & Webhook System
- **Idempotency key dedup** — dedupe repeated hook requests by idempotency key. (`#44438`)
- **Hooks fail-closed on path errors** — skip unresolvable workspace hook paths instead of falling back. (`#44437`)
- **Hook auth bucketing** — bucket failures by forwarded client IP and warn when `allowedAgentIds` is unrestricted.
- **Plugin hook context parity** — pass `trigger` and `channelId` through embedded hook contexts. (`#42362`)

---

## 🟡 MEDIUM RELEVANCE — Plugin & Extensibility

### Plugin SDK
- **Provider-plugin architecture** — Ollama, vLLM, and SGLang moved onto provider-plugin architecture with provider-owned onboarding/discovery/hooks.
- **`sendPayload` outbound adapters** — shared adapter support across Discord, Slack, WhatsApp, Zalo with multi-media iteration. (`#22382`)
- **`before_prompt_build` context fields** — `prependSystemContext` and `appendSystemContext` for provider caching.
- **Plugin onboarding flows** — `configureInteractive` and `configureWhenConfigured` hooks for channel plugins.
- **Runtime STT** — `api.runtime.stt.transcribeAudioFile(...)` for audio transcription. (`#22402`)
- **Runtime heartbeat trigger** — `runtime.system.requestHeartbeatNow(...)` to wake sessions immediately. (`#19464`)
- **Agent event subscriptions** — `runtime.events.onAgentEvent` and `onSessionTranscriptUpdate`. 
- **Session lifecycle keys** — include `sessionKey` in `session_start`/`session_end` hook events. (`#26394`)
- **Channel/binding collision detection** — fail fast on collisions. (`#45628`)

### Configuration Hardening
- **Config validation** — accept `agents.list[].params` overrides, web fetch readability/firecrawl, Signal groups, discovery wideArea domain.
- **Workspace plugin auto-load disabled** — cloned repos cannot execute plugin code without trust decision. (`GHSA-99qw-6mr3-36qr`)

---

## 🔵 MEDIUM RELEVANCE — Tools & Browser

### Browser Integration
- **Chrome DevTools MCP attach mode** — official attach mode for signed-in live Chrome sessions.
- **Built-in browser profiles** — `profile="user"` for logged-in host browser, `profile="chrome-relay"` for extension relay.
- **Batched browser actions** — selector targeting, delayed clicks, normalized batch dispatch.
- **Existing-session hardening** — transport errors trigger reconnects, tool-level errors preserve session. (`#45682`)

### Tools  
- **PDF tool** — first-class `pdf` tool with native Anthropic and Google PDF provider support plus extraction fallback. 
- **Brave LLM Context mode** — `tools.web.search.brave.mode: "llm-context"` for grounding snippets.
- **Perplexity citation recovery** — recover citations from `message.annotations`. (`#40881`)
- **Diffs plugin tool** — read-only diff rendering from before/after text with gateway viewer URLs and PNG output.
- **Web search: Kimi (Moonshot) provider** — new `provider: "kimi"` option.

---

## 🟢 LOWER RELEVANCE — Security Hardening

(Listed for awareness — many are platform-specific exec approval hardening)

- **Single-use setup codes** for device pairing
- **Workspace plugin trust boundary** — disable implicit auto-load
- **Exec approval Unicode hardening** — escape zero-width characters, normalize fullwidth
- **POSIX case-sensitive exec allowlists** — prevent cross-case overmatch
- **Sender ownership for `/config`** — block non-owner access
- **Scope clearing on shared-token WebSocket** — prevent self-declared elevated scopes
- **Browser profile create/delete gating** — block from `browser.request`
- **Gateway workspace boundary enforcement** — reject spawned-run lineage overrides
- **Sandbox session-tree visibility** — enforce access guards before mutating session state
- **SHA-256 synthetic IDs** — replace SHA-1 for gateway lock and tool-call IDs
- **Pre-auth WebSocket frame limits** — reject oversized pre-auth frames
- **Feishu/LINE/Zalo webhook hardening** — various signature/auth improvements

---

## 📱 APPENDIX — Platform-Specific (iOS, macOS, Android, Windows)

Not directly applicable to our server-side system, listed for completeness:

| Platform | Notable Changes |
| -------- | --------------- |
| iOS | Home canvas redesign, TestFlight beta flow, watch app mirroring, TTS prefetch, chat UI cleanup |
| macOS | Chat model picker, thinking-level persistence, onboarding remote gateway detection, exec approvals in gateway prompter |
| Android | Chat settings redesign, Google Code Scanner for onboarding, HttpURLConnection leak fix |
| Windows | Gateway install/stop/status fixes, console window suppression, native update fixes |

---

## 📊 Release Timeline

| Release | Date | Files Changed | Key Theme |
| ------- | ---- | ------------- | --------- |
| v2026.2.21 | Feb 21 | — | ACP dispatch default-on, plugin SDK expansions |
| v2026.2.22 | Feb 23 | 300 | PDF tool, Diffs tool, outbound adapters |
| v2026.2.23 | Feb 24 | 300 | Security/onboarding trust model, Brave LLM context |
| v2026.2.24 | Feb 25 | — | Minor patch |
| v2026.2.25 | Feb 26 | 11 | Discord subagent threads |
| v2026.2.26 | Feb 27 | 300 | Plugin hook policy, provider-plugin arch prep |
| v2026.3.1 | Mar 2 | 300 | Context engine, K8s docs, cron tightening |
| v2026.3.2 | Mar 3 | 23 | Security hardening sprint |
| v2026.3.7 | Mar 8 | 34 | Memory multimodal, token sanitization |
| v2026.3.8 | Mar 9 | 7 | Developer experience, session routing fix |
| v2026.3.11 | Mar 12 | 5 | WebSocket origin validation |
| v2026.3.12 | Mar 13 | 300 | Dashboard v2, fast mode, security mega-release |
| v2026.3.13-1 | Mar 14 | 14 | Compaction fixes, browser profiles |

---

## Next Steps

This baseline establishes our starting point. The biweekly automated pipeline will now detect any releases **after** v2026.3.13-1 and produce incremental reports. For deeper investigation of specific features, see the full release data in `artifacts/openclaw-sync/2026-03-21/release_report.json`.
