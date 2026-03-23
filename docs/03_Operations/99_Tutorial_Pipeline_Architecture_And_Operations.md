# Tutorial Pipeline — Architecture & Operations

> **Canonical source-of-truth** for the YouTube Tutorial Pipeline. All other
> tutorial-related documentation should reference this file.

## 1. Pipeline Overview

The YouTube Tutorial Pipeline is an automated system that watches YouTube playlists for new videos, ingests their content (transcripts, metadata), dispatches an AI agent session to generate a structured tutorial artifact, optionally bootstraps a GitHub repository for the tutorial code, and notifies stakeholders at each stage.

```
Playlist Watch → New Video Detection → Webhook Dispatch → Agent Session
  → Transcript Ingest → Tutorial Generation → Repo Bootstrap → Notification
```

### Key Components

| Layer | File | Responsibility |
|-------|------|---------------|
| Playlist Watcher | `services/youtube_playlist_watcher.py` | Periodically polls configured YouTube playlists for new video IDs |
| Hooks Service | `hooks_service.py` | Receives webhook events, manages dispatch queue, throttling, retry policies |
| Gateway Server | `gateway_server.py` | Notification system, tutorial dashboard API, persistence |
| YouTube Ingestion | `youtube_ingest.py` | Fetches transcripts via rotating proxies (VPS fallback path) |
| Desktop Transcript Worker | `desktop_transcript_worker.py` | **Primary** transcript fetching from desktop residential IP, proxy fallback, VPS writeback |
| Telegram Notifier | `services/tutorial_telegram_notifier.py` | Per-video dedup Telegram alerts for tutorial events |
| Dashboard (Tutorials) | `web-ui/app/dashboard/tutorials/page.tsx` | Tutorial runs, review jobs, bootstrap jobs, notifications with client-side dedup |
| Dashboard (Main) | `web-ui/app/dashboard/page.tsx` | Notification panel with video-level dedup |

---

## 2. Notification Kinds

All tutorial pipeline notification kinds are defined in `_TUTORIAL_NOTIFICATION_KINDS` in `gateway_server.py`:

| Kind | Stage | Description |
|------|-------|-------------|
| `youtube_playlist_new_video` | Detection | New video found in watched playlist |
| `youtube_playlist_dispatch_failed` | Detection | Failed to dispatch webhook for new video |
| `youtube_tutorial_started` | Processing | Agent session started for the video |
| `youtube_tutorial_progress` | Processing | Progress update during tutorial generation |
| `youtube_tutorial_interrupted` | Processing | Agent session was interrupted (timeout, error) |
| `youtube_tutorial_ready` | Complete | Tutorial artifact generated successfully |
| `youtube_tutorial_failed` | Complete | Tutorial generation failed |
| `youtube_ingest_failed` | Ingestion | Transcript ingestion failed |
| `youtube_ingest_proxy_alert` | System Health | Proxy rotation failure (global, not per-video) |
| `youtube_hook_recovery_queued` | Recovery | Recovery queued for a previously failed hook |
| `tutorial_repo_bootstrap_queued` | Bootstrap | GitHub repo creation queued |
| `tutorial_repo_bootstrap_ready` | Bootstrap | Repo created and pushed successfully |
| `tutorial_repo_bootstrap_failed` | Bootstrap | Repo bootstrap failed |

---

## 3. Notification Deduplication

The pipeline generates multiple notifications per video as it moves through stages. Without deduplication, the notification panel becomes cluttered with stale intermediate stages. Deduplication operates at three levels:

### 3.1 Backend — Video-Level Upsert (`gateway_server.py`)

`_TUTORIAL_PIPELINE_STAGE_KINDS` defines the subset of kinds that track a single video through the pipeline. When `_add_notification()` receives a notification for one of these kinds and the metadata contains a `video_id` (or `video_key`):

1. It scans existing non-dismissed notifications for any with a matching `video_id` whose kind is also in `_TUTORIAL_PIPELINE_STAGE_KINDS`.
2. If found, the existing notification is **updated in-place** with the new kind, title, message, and metadata.
3. No new notification row is created — the video always has at most one active notification.

This mirrors the existing health-alert kind-level upsert pattern but groups by `video_id` across multiple pipeline kinds.

**Edge cases:**
- **Dismissed notifications** are skipped — a new row is created alongside the dismissed one.
- **No video_id** — fallback to normal creation (no dedup).
- **System health alerts** (`youtube_ingest_proxy_alert`) are excluded from pipeline stage dedup — they use kind-level upsert instead.

### 3.2 Frontend — Dashboard Notification Panel (`page.tsx`)

The `visibleNotifications` memo in the dashboard page applies a post-filter dedup step: after status/category/severity filters, tutorial notifications are grouped by `tutorialVideoKey()` and only the latest per video is retained. Non-tutorial notifications pass through unchanged.

### 3.3 Frontend — Tutorials Tab (`tutorials/page.tsx`)

The tutorials tab has its own `visibleNotifications` memo that uses `notificationEntityKey()` to group by video and keeps only the latest notification per entity.

### 3.4 Telegram — Per-Video Cooldown (`tutorial_telegram_notifier.py`)

`TutorialTelegramNotifier.maybe_send()` implements per-video dedup for `youtube_tutorial_ready` and `youtube_playlist_new_video` kinds using a TTL cache, preventing duplicate Telegram messages within a cooldown window.

---

## 4. Transcript Ingestion

Transcript fetching uses a **two-tier architecture**:

### 4.1 Primary: Desktop Transcript Worker (Residential IP)

> [!IMPORTANT]
> The desktop transcript worker is a **standalone program** that runs on Kevin's desktop,
> NOT a UA cron job. The desktop must be running for this functionality to work.

The `desktop_transcript_worker.py` module fetches YouTube transcripts from the desktop's residential IP address — no proxy needed. It connects to the VPS CSI database via SSH to:
1. Query videos with `transcript_status='failed'`
2. Fetch transcripts locally via `youtube-transcript-api`
3. Write successful transcripts back to the CSI database

Key behaviors:
- **Local-first**: Uses residential IP, avoiding datacenter anti-bot detection
- **Proxy fallback**: Falls back to Webshare rotating proxy on local failure (default: ON)
- **Rate limiting**: Configurable delay between requests (default: 5s)
- **Circuit breaker**: Trips after N consecutive failures, aborts after M trips
- **Batch caps**: `batch_size` and `daily_cap` limits with loud banners
- **Loud failure classification**: Three distinct failure types with unmissable banners:
  - `⚠️ SELF-IMPOSED CAP HIT` — our own limits, not YouTube
  - `🚫 YOUTUBE BLOCK DETECTED` — bot detection, rate limiting, etc.
  - `🔌 CIRCUIT BREAKER` — systematic failure, pausing/aborting

Run modes:
```bash
# Test specific videos (no VPS interaction)
uv run python src/universal_agent/desktop_transcript_worker.py --test <VIDEO_IDs>

# Dry run — show what VPS batch would process
uv run python src/universal_agent/desktop_transcript_worker.py --batch --dry-run

# Live batch — query VPS, fetch, write back
DTW_BATCH_SIZE=10 uv run python src/universal_agent/desktop_transcript_worker.py --batch
```

### 4.2 Fallback: VPS Rotating Proxy (Legacy Path)

The VPS-side proxy path (`youtube_ingest.py`) remains available when `UA_HOOKS_YOUTUBE_INGEST_MODE=proxy` is set. This is the legacy path and is now secondary to the desktop worker. It uses Webshare rotating residential proxies and is cost-sensitive.

Key behaviors:
- Configurable retry with exponential backoff and jitter
- Minimum character threshold to detect empty/stub transcripts
- Cooldown between ingestion attempts for the same video
- In-flight TTL tracking to avoid double-processing
- Proxy failure generates `youtube_ingest_proxy_alert` (health-alert dedup)

---

## 5. Webhook Dispatch & Retry

The hooks service manages a bounded dispatch queue with configurable concurrency:

- **Queue limit:** `UA_HOOKS_AGENT_DISPATCH_QUEUE_LIMIT` (default: 40)
- **Concurrency:** `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY` (default: 1–2)
- **Overflow notification:** cooldown-gated `hook_dispatch_queue_overflow` alert
- **Dispatch dedup:** TTL-based dedup prevents re-dispatching the same video within `UA_HOOKS_YOUTUBE_DISPATCH_DEDUP_TTL_SECONDS` (default: 3600)
- **Retry policies:** Configurable per-kind via `UA_HOOKS_DISPATCH_RETRY_POLICIES` (JSON)
- **Startup recovery:** Re-queues recently interrupted sessions on restart (`UA_HOOKS_STARTUP_RECOVERY_ENABLED`)

---

## 6. Repo Bootstrap

After tutorial generation, the pipeline can automatically create a GitHub repository:
- `tutorial_repo_bootstrap_queued` → `tutorial_repo_bootstrap_ready` / `tutorial_repo_bootstrap_failed`
- Managed via the tutorials dashboard "Bootstrap Jobs" section

---

## 7. Configuration (Environment Variables)

### Core Hooks
| Variable | Default | Description |
|----------|---------|-------------|
| `UA_HOOKS_ENABLED` | `""` | Enable/disable the hooks service |
| `UA_HOOKS_TOKEN` | `""` | Authentication token for webhook endpoints |
| `UA_HOOKS_AUTO_BOOTSTRAP` | `""` | Enable automatic repo bootstrap after tutorial generation |

### YouTube Ingestion
| Variable | Default | Description |
|----------|---------|-------------|
| `UA_HOOKS_YOUTUBE_INGEST_MODE` | `""` | Ingestion mode: `proxy` for rotating proxies |
| `UA_HOOKS_YOUTUBE_INGEST_URL` | `""` | Primary ingest endpoint URL |
| `UA_HOOKS_YOUTUBE_INGEST_URLS` | `""` | Comma-separated fallback URLs |
| `UA_HOOKS_YOUTUBE_INGEST_TOKEN` | `""` | Auth token for ingest endpoints |
| `UA_HOOKS_YOUTUBE_INGEST_TIMEOUT_SECONDS` | `120` | Ingest request timeout |
| `UA_HOOKS_YOUTUBE_INGEST_RETRY_ATTEMPTS` | varies | Number of retry attempts |
| `UA_HOOKS_YOUTUBE_INGEST_MIN_CHARS` | `160` | Minimum transcript length |
| `UA_HOOKS_YOUTUBE_INGEST_COOLDOWN_SECONDS` | `600` | Cooldown between same-video ingests |
| `UA_HOOKS_YOUTUBE_INGEST_INFLIGHT_TTL_SECONDS` | `900` | In-flight tracking TTL |
| `UA_HOOKS_YOUTUBE_INGEST_FAIL_OPEN` | `false` | Continue pipeline on ingest failure |

### Dispatch
| Variable | Default | Description |
|----------|---------|-------------|
| `UA_HOOKS_AGENT_DISPATCH_CONCURRENCY` | `1` | Max concurrent agent dispatches |
| `UA_HOOKS_AGENT_DISPATCH_QUEUE_LIMIT` | `40` | Max queued dispatches |
| `UA_HOOKS_YOUTUBE_DISPATCH_DEDUP_TTL_SECONDS` | `3600` | Dispatch dedup window |
| `UA_HOOKS_YOUTUBE_TIMEOUT_SECONDS` | `1800` | Session timeout for tutorial processing |
| `UA_HOOKS_DISPATCH_RETRY_POLICIES` | `{}` | JSON retry policies per kind |

### Notifications
| Variable | Default | Description |
|----------|---------|-------------|
| `UA_NOTIFICATIONS_MAX` | `500` | Max in-memory notification count |
| `YOUTUBE_TUTORIAL_TELEGRAM_CHAT_ID` | `""` | Telegram chat ID for tutorial alerts |
| `TELEGRAM_BOT_TOKEN` | `""` | Telegram bot token |

### Startup Recovery
| Variable | Default | Description |
|----------|---------|-------------|
| `UA_HOOKS_STARTUP_RECOVERY_ENABLED` | `true` | Re-queue interrupted sessions on startup |
| `UA_HOOKS_STARTUP_RECOVERY_MAX_SESSIONS` | `3` | Max sessions to recover |
| `UA_HOOKS_STARTUP_RECOVERY_MIN_AGE_SECONDS` | `120` | Min age for recovery candidates |
| `UA_HOOKS_STARTUP_RECOVERY_COOLDOWN_SECONDS` | `1800` | Cooldown between recovery attempts |

### Desktop Transcript Worker
| Variable | Default | Description |
|----------|---------|-------------|
| `DESKTOP_TRANSCRIPT_WORKER_ENABLED` | `true` | Kill switch for the desktop worker |
| `DTW_BATCH_SIZE` | `25` | Max videos per batch run |
| `DTW_DAILY_CAP` | `200` | Max videos per day |
| `DTW_DELAY_SECONDS` | `5.0` | Delay between transcript requests |
| `DTW_PROXY_FALLBACK` | `true` | Fall back to rotating proxy on local failure |
| `DTW_MAX_CONSECUTIVE_FAILURES` | `3` | Circuit breaker threshold |
| `DTW_CIRCUIT_BREAKER_COOLDOWN` | `60.0` | Pause after breaker trips (seconds) |
| `DTW_MAX_CIRCUIT_BREAKER_TRIPS` | `2` | Abort batch after N trips |
| `DTW_VPS_HOST` | `root@uaonvps` | SSH target for VPS CSI database |
| `DTW_CSI_DB_PATH` | `/var/lib/universal-agent/csi/csi.db` | CSI database path on VPS |

---

## 8. Key Source Files

| Path | Description |
|------|-------------|
| `src/universal_agent/hooks_service.py` | Core pipeline orchestration, dispatch queue, retry |
| `src/universal_agent/gateway_server.py` | Notification CRUD, tutorial dashboard API, dedup constants |
| `src/universal_agent/youtube_ingest.py` | Transcript fetching, proxy rotation (VPS fallback path) |
| `src/universal_agent/desktop_transcript_worker.py` | **Primary** desktop residential IP transcript worker |
| `src/universal_agent/services/youtube_playlist_watcher.py` | Playlist polling |
| `src/universal_agent/services/tutorial_telegram_notifier.py` | Telegram notification sink with per-video dedup |
| `web-ui/app/dashboard/page.tsx` | Main dashboard with notification dedup |
| `web-ui/app/dashboard/tutorials/page.tsx` | Tutorials tab with entity-level dedup |
| `tests/unit/test_tutorial_notification_dedup.py` | Backend video-level dedup tests |
| `tests/unit/test_tutorial_telegram_dedup.py` | Telegram notifier dedup tests |
| `tests/test_desktop_transcript_worker.py` | Desktop worker unit tests (37 tests) |

---

## 9. Dashboard API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/dashboard/tutorials/runs` | GET | List tutorial run directories |
| `/api/v1/dashboard/tutorials/runs/{path}` | DELETE | Delete a tutorial run directory |
| `/api/v1/dashboard/tutorials/notifications` | GET | Tutorial-filtered notifications |
| `/api/v1/dashboard/tutorials/review-jobs` | GET | Pending review dispatches |
| `/api/v1/dashboard/tutorials/bootstrap-jobs` | GET | Repo bootstrap job status |
| `/api/v1/hooks/youtube/dispatch` | POST | Manually trigger a tutorial dispatch |
| `/api/v1/hooks/youtube/retry` | POST | Retry a failed hook |

---

## 10. Proxy Failure Semantics and Debugging Notes

The VPS fallback path in `src/universal_agent/youtube_ingest.py` builds its
Webshare configuration from the **current process environment** inside
`_build_webshare_proxy_config()`.

The relevant variables are:

- `PROXY_USERNAME` or `WEBSHARE_PROXY_USER`
- `PROXY_PASSWORD` or `WEBSHARE_PROXY_PASS`
- optional host, port, and location filters

### What `proxy_not_configured` Actually Means

`proxy_not_configured` does **not** always mean "Infisical never had the
secret."

It specifically means `_build_webshare_proxy_config()` could not find a usable
username/password pair in the current gateway process at request time.

That can happen for multiple reasons:

1. Infisical bootstrap failed or never ran
2. the wrong deployment profile allowed degraded fallback startup
3. secret names or aliases are inconsistent
4. later runtime code mutated `os.environ` and removed the proxy vars from the
   live process after bootstrap

### Live Service vs Fresh Process Matters

During the 2026-03-23 production incident, a fresh process on the VPS could
bootstrap proxy secrets successfully while the long-running gateway process was
still returning:

- `error = "proxy_not_configured"`
- `proxy_mode = "disabled"`

That narrowed the fault to **post-bootstrap runtime mutation**, not secret
storage alone.

The final fix lived in `src/universal_agent/execution_engine.py`:

- child-process env sanitization remains in place for Claude SDK subprocess
  spawn
- the parent gateway env is now restored immediately afterward
- the main `process_turn()` path no longer strips proxy credentials from the
  live service

### Operational Rule

When investigating tutorial ingest failures, compare:

1. the live HTTP endpoint behavior on the target host
2. a fresh one-off bootstrap process on that same host

If those disagree, inspect runtime code that mutates `os.environ` after
bootstrap. Do not assume a successful fresh bootstrap proves the long-running
service still has the same environment later.
