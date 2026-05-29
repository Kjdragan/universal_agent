# HANDOFF — YouTube Feeds & CSI Topology (2026-05-29)

> **Purpose:** Self-contained handoff for a parallel session working through how the
> **CSI process relates to YouTube**. Everything here is code-verified (file:line
> citations included). Terminology marked *(proposed)* is not yet ratified by Kevin —
> treat as working names, not canon.
> **Source repo:** `/home/kjdragan/lrepos/universal_agent` (all paths below are relative
> to `src/universal_agent/` unless noted).
>
> **Headline finding:** There are **two distinct YouTube feeds** that read from the
> **same config file** but feed **two completely separate pipelines** with radically
> different volume and health. One works (the gold Daily Digest); the other is the
> source of a large parked/cancelled backlog (the convergence/ideation pipeline over
> the full ~444-channel watchlist).

---

## 0. TL;DR map

```
                 channels_watchlist.json   (ONE shared config, 443 channels, tiered)
                 tiers: gold=22 · sidecar=417 · blocked=4
                          │
         ┌────────────────┴───────────────────────────────┐
         │ slice = gold (22)                               │ slice = ALL (444)
         ▼                                                 ▼
 ┌─────────────────────────┐                  ┌──────────────────────────────────┐
 │ FEED 1 — DAILY DIGEST    │                  │ FEED 2 — CONVERGENCE / IDEATION   │
 │ (curated, bounded) OK    │                  │ (broad, hourly, firehose) WARN     │
 ├─────────────────────────┤                  ├──────────────────────────────────┤
 │ gold_channel_poller 5:30 │                  │ CSI ingester polls youtube_channel│
 │   -> <DAY>_YT_PLAYLIST   │                  │   _rss hourly-ish per channel     │
 │   cap 10/day             │                  │   -> CSI events + rss_event_analys │
 │ youtube_daily_digest 6:00│                  │ sync_topic_signatures_from_csi    │
 │   -> 1 synthesized digest│                  │   -> proactive_topic_signatures   │
 │   -> CSI record          │                  │ +-- Convergence Detection (Track A)│
 │   -> tutorial dispatch   │                  │ +-- Ideation Sweep        (Track B)│
 │   -> delete processed    │                  │   -> convergence_candidate -> Atlas│
 │                          │                  │   -> /evaluate-and-author -> digest│
 └─────────────────────────┘                  └──────────────────────────────────┘
                                                  + LEGACY insight_detection emitter
                                                    (deprecated, still bleeding out)
```

---

## 1. The shared config: `channels_watchlist.json`

- Repo copy: `channels_watchlist.json` (repo root). Production copy:
  `/var/lib/universal-agent/csi/channels_watchlist.json`
  (`api/routers/csi_watchlist.py:16` `_DEFAULT_WATCHLIST_FILE`; local fallback
  `CSI_Ingester/development/channels_watchlist.json` at `csi_watchlist.py:135,272`).
- Shape: `{ extraction_date, playlist_id, total_videos_found, unique_channels, channels[] }`
  - `playlist_id = PLjL3liQSixtvyu1yGb6IOwPUPMKeS067E` (the master tracking playlist)
  - `unique_channels = 443`, `total_videos_found = 930`
- Per-channel fields: `channel_id, channel_name, video_count, rss_feed_url, youtube_url,
  domain, _categorization_method, tier, manual_add_count_30d,
  sidecar_approval_count_30d, last_publication_seen_at, last_promoted_to_gold_at,
  duration_max_seconds_override`
- **Tier distribution (verified):** `gold=22 · sidecar=417 · blocked=4`
- **Tier meaning:**
  - `gold` = core curated channels (feed the Daily Digest)
  - `sidecar` = wide net, "might be interesting" (only feed convergence)
  - `blocked` = excluded
- **Promotion model:** sidecar -> gold is driven by the metric fields
  (`manual_add_count_30d`, `sidecar_approval_count_30d`, `last_promoted_to_gold_at`).
- RSS URL pattern: `https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}`
  (`csi_watchlist.py:331`).

---

## 2. FEED 1 — The Daily YouTube Digest (gold-only, curated) — HEALTHY

**Slice:** the **22 `gold`** channels only.

**Chain:**
1. `services/youtube_gold_channel_poller.py` — runs **~5:30 AM America/Chicago**, ~30 min
   before the digest cron. For each `tier="gold"` channel, fetches its RSS, routes each
   new video into a **day-of-week playlist** by the video's *published* weekday
   (`MONDAY_YT_PLAYLIST` ... `SUNDAY_YT_PLAYLIST`).
   - Daily cap: `UA_YOUTUBE_GOLD_DAILY_CAP` (**default 10**), newest-first across all gold
     channels so one channel can't crowd out others.
   - Per-channel `duration_max_seconds_override` overrides global duration triage.
   - Dedup: drops video if already in target playlist OR in local `processed_videos`
     sqlite.
2. `scripts/youtube_daily_digest.py` — runs **~6:00 AM** via UA Cron. Steps (from its
   module docstring):
   1. select today's day-of-week playlist
   2. extract transcripts (residential proxies, graceful fallback)
   3. synthesize compressed retelling + meta-analysis via the ZAI inference path
   4. save markdown artifact to the `daily_digests` workspace
   5. **emit the digest as a CSI record** (so it appears in the CSI Feed dashboard and is
      visible to the proactive-signal pipeline)
   6. save a ranked tutorial-candidate decision artifact
   7. **dispatch top code-implementation prospects to the YouTube tutorial pipeline**
      (-> `tutorial_build` tasks; see `services/proactive_tutorial_builds.py`)
   8. save a repopulate pocket for processed videos
   9. **delete processed videos from the playlist** (clean-inbox pattern)
   - Playlist IDs stored in Infisical as `<DAY>_YT_PLAYLIST`.
3. `services/youtube_playlist_manager.py` — YouTube Data API v3 wrapper used by the above.
   OAuth2 creds: `YOUTUBE_OAUTH_CLIENT_ID / _SECRET / _REFRESH_TOKEN` (Infisical).
   Setup script: `scripts/youtube_oauth2_setup.py`.

**Volume / health:** ~10 videos/day -> **one** digest. Bounded, self-cleaning, no parking.
This is the **model that works** — bounded source -> hard cap -> single synthesis -> clean
inbox.

---

## 3. FEED 2 — Convergence / Ideation (full 444-channel watchlist) — FIREHOSE

**Slice:** the **entire ~444-channel watchlist** (gold + all 417 sidecar).
`services/invariants/csi_source_liveness.py:42` is explicit:
`"youtube_channel_rss": 12.0,  # 444-channel watchlist, hourly-ish per channel`.

**Chain:**
1. **CSI ingester** polls `youtube_channel_rss` for every watchlist channel, hourly-ish,
   producing CSI `events` (`source='youtube_channel_rss'`) + `rss_event_analysis` rows
   (transcript + summary). *(The CSI ingester is the `csi-ingester` systemd service; the
   CSI DB path is resolved via `gateway_server._csi_default_db_path()`.)*
2. `services/proactive_convergence.py:204` **`sync_topic_signatures_from_csi`** — the
   active entrypoint. Reads CSI events `WHERE source='youtube_channel_rss'` JOIN
   `rss_event_analysis` (transcript_status, summary), and `upsert_topic_signature(...)`
   into the **`proactive_topic_signatures`** corpus (one row per video, deduped by
   video_id).
3. **Two detectors run over that corpus, same stage:**
   - **Convergence Detection** *(proposed; was "Track A")* —
     `track_a_concrete_convergence` (`:1173`, "Fast Filter -> Deep Semantic Comparison ->
     Quality Gate"). Finds the **SAME story across >=2 channels**. The code itself flags
     this as **low value**: *"convergence detection finds the SAME story across channels —
     which is, by construction, news saturation (low marginal value)"* (`:613`).
   - **Ideation Sweep** *(proposed; was "Track B")* — `track_b_ideation_synthesis`
     (`:1264`, "LLM Ideation / Synthesis on a batch of schemas"). Finds **non-obvious
     cross-cutting patterns** — the **high-value** engine. Chunks corpus into batches of
     20. Restored 2026-05-29 (ZAI quota abundant); see
     `docs/proactive_signals/ideation_sweep_2026-05-29.md`.
     Flags: `UA_IDEATION_SWEEP_ENABLED` (default 1), `UA_IDEATION_MIN_CONFIDENCE`
     (default 0.7).
4. **Active output:** both detectors write `convergence_candidate` rows via
   `write_convergence_candidate` (ideation rows tagged `candidate_kind='ideation'`),
   deduped + write-once. These route to **Atlas** via the
   `/evaluate-and-author-intel-brief` skill, then to the consolidated **Insight Digest**
   email (`hourly_insight_email` cron; `services/digest_delivery_reminder.py`).
   - `convergence_candidate` health (activity_state.db): **58 completed, 11 delegated,
     12 parked** — i.e. this active path basically **works**.
5. **Cron trigger:** `gateway_server.py:19596` `_ensure_csi_convergence_cron_job` ->
   calls `sync_topic_signatures_from_csi`. The cron is the cadence governor (the sync
   runs convergence every call regardless of new signatures).

---

## 4. The LEGACY firehose (root of the parked/cancelled backlog)

- `detect_and_queue_convergence` / `detect_and_queue_convergence_llm`
  (`proactive_convergence.py:1015/1047`) is the **OLD per-signature** path. For each
  signature it runs both tracks and, in the Track-B loop, calls
  **`create_insight_brief_task`** (`:1416`) which emits **one Task Hub item per insight**
  with `source_kind='insight_detection'` (`:1497`) — a full VP authoring mission queued
  for `vp.general.primary`.
- **Status:** lines `:221-224` state it is **NOT invoked by the active pipeline anymore**
  — *"remains callable from the gateway's two hand-trigger convergence endpoints until
  PR E cleans it up."* Endpoints: `gateway_server.py:~21084` and `~21133/21140`.
- **Consequence (activity_state.db, `insight_detection`):**
  **698 cancelled · 30 parked · 1 completed** -> **0.14% completion**. Created at
  **142–220/day** (5/25->5/28, declining as the legacy path bleeds out). No dedup, no
  per-run cap, no backpressure vs. a single consumer -> the overflow is stale-reaped to
  `cancelled` / `parked`.
- **Net:** parking here is the *overflow drain* of a deprecated producer that fanned every
  synthesized insight into an individual, uncatchable work unit. This is an **incomplete
  migration** (legacy emitter not yet removed), layered on a **producer/consumer capacity
  mismatch**.

---

## 5. Proposed terminology (replaces "Track A / Track B" — NOT yet ratified)

| Old / code term | Proposed canonical name | What it is |
|---|---|---|
| (Feed 1 source) | **Gold Poll** | 5:30 AM RSS pull of the 22 gold channels -> day playlists |
| Daily Digest engine | **Daily YouTube Digest** | 6:00 AM transcript+synthesis of the day playlist (Feed 1) |
| (Feed 2 source) | **Watchlist RSS Ingestion** | CSI hourly poll of all 444 channels -> `youtube_channel_rss` |
| `sync_topic_signatures_from_csi` | **Signature Sync** | RSS analysis -> `proactive_topic_signatures` corpus |
| Track A / `track_a_concrete_convergence` | **Convergence Detection** | same-story-across-channels (news saturation, LOW value) |
| Track B / `track_b_ideation_synthesis` | **Ideation Sweep** | non-obvious cross-cutting synthesis (HIGH value) |
| Atlas evaluate skill | **Candidate Evaluation** | Atlas scores a `convergence_candidate`, authors brief |
| `hourly_insight_email` | **Insight Digest** | consolidated email delivery of authored briefs |
| `detect_and_queue_convergence` -> `insight_detection` | **Legacy Insight-Brief Emitter** *(deprecated, PR E)* | the per-insight firehose |

> Naming is **still open** — Kevin paused a grilling session before ratifying. The
> alternative for the two detectors was output-named ("Saturation Detector" /
> "Pattern Synthesizer"). Confirm with Kevin before treating any of these as canon.

---

## 6. Common misconceptions (corrected)

- WRONG: "Track B = RSS feed checking / evaluating videos." -> RSS checking is **upstream
  of both tracks** (Watchlist RSS Ingestion + Signature Sync). Both tracks read the
  *already-extracted* signature corpus; neither touches a feed.
- WRONG: "Track A = the Daily YouTube Digest / creating briefings." -> Track A is only the
  same-story detector. The Daily Digest is **Feed 1**, a separate gold-only pipeline.
  Briefing/authoring is **downstream** (Candidate Evaluation -> Insight Digest).
- WRONG: "There's one YouTube feed." -> Two feeds off one config: gold-slice (Feed 1,
  <=10/day, healthy) and full-watchlist-slice (Feed 2, ~444 channels hourly, firehose).

---

## 7. Useful DB queries / facts

> **DB split-brain caveat:** the morning briefing counted `task_hub.db`
> (`/opt/universal_agent/AGENT_RUN_WORKSPACES/task_hub.db`, smaller, 19 parked
> `proactive_signal`), but the heartbeat/watchdog read
> `/opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db` (canonical,
> `get_activity_db_path()`, **1,534 parked total**). They are different files with
> different populations. Always confirm which DB a number came from.

Key tables (proactive_convergence schema): `proactive_topic_signatures` (the corpus),
`convergence_candidates` (active output), `proactive_convergence_events`.

```bash
# parked/cancelled lifecycle for the legacy firehose (canonical DB):
ssh ua@uaonvps 'sqlite3 /opt/universal_agent/AGENT_RUN_WORKSPACES/activity_state.db \
  "SELECT source_kind, status, COUNT(*) FROM task_hub_items \
   WHERE source_kind IN (\"insight_detection\",\"convergence_candidate\",\"proactive_signal\") \
   GROUP BY source_kind, status ORDER BY 1,2;"'

# tier distribution of the shared watchlist:
python3 -c "import json,collections;d=json.load(open('channels_watchlist.json'));\
print(collections.Counter(c.get('tier') for c in d['channels']))"
```

---

## 8. Open questions for the next session

1. **Does anyone actually consume Feed 2's output?** Do `convergence_candidate` /
   `insight_detection` briefs reach Kevin, or is the whole 444-channel convergence feed
   producing intelligence nobody reads? (Check delivered Insight Digest emails vs. items
   produced.)
2. **Should the sidecar->convergence feed run at all,** or be narrowed/capped to match
   consumer capacity? The Daily Digest (gold) is the proven bounded model.
3. **Finish PR E:** remove the Legacy Insight-Brief Emitter and the two hand-trigger
   endpoints so `insight_detection` stops being created.
4. **CSI DB location:** confirm exact path of the CSI events DB feeding
   `youtube_channel_rss` (resolved at runtime via `gateway_server._csi_default_db_path()`).
5. **Ratify terminology** (section 5) with Kevin before writing it into any canonical doc.

---

## 9. Code citation index

| File | Symbol / line | Role |
|---|---|---|
| `channels_watchlist.json` | repo root + `/var/lib/universal-agent/csi/` | shared tiered channel config |
| `services/youtube_gold_channel_poller.py` | module | Feed 1 — Gold Poll (5:30 AM, cap 10) |
| `scripts/youtube_daily_digest.py` | module | Feed 1 — Daily YouTube Digest (6:00 AM) |
| `services/youtube_playlist_manager.py` | module | YouTube Data API v3 / OAuth2 wrapper |
| `api/routers/csi_watchlist.py` | `:16,135,272,331,578` | watchlist file resolution + RSS URL |
| `services/invariants/csi_source_liveness.py` | `:42` | "444-channel watchlist, hourly-ish per channel" |
| `services/proactive_convergence.py` | `:204 sync_topic_signatures_from_csi` | Feed 2 — Signature Sync (active entrypoint) |
| `services/proactive_convergence.py` | `:613` | comment: convergence = news saturation, low value |
| `services/proactive_convergence.py` | `:1173 track_a_concrete_convergence` | Convergence Detection |
| `services/proactive_convergence.py` | `:1264 track_b_ideation_synthesis` | Ideation Sweep |
| `services/proactive_convergence.py` | `:1015/1047 detect_and_queue_convergence[_llm]` | LEGACY firehose path |
| `services/proactive_convergence.py` | `:1416 create_insight_brief_task` -> `:1497` | emits `insight_detection` task/insight |
| `services/proactive_convergence.py` | `:221-224` | legacy path deprecated, PR E cleanup |
| `gateway_server.py` | `:19596 _ensure_csi_convergence_cron_job` | Feed 2 cron trigger |
| `gateway_server.py` | `:~21084 / :~21133` | two legacy hand-trigger convergence endpoints |
| `gateway_server.py` | `:19267 _ensure_hourly_insight_email_cron_job` | Insight Digest delivery cron |
| `services/proactive_tutorial_builds.py` | module | `tutorial_build` tasks (from Daily Digest step 7) |
| `docs/proactive_signals/ideation_sweep_2026-05-29.md` | doc | Ideation Sweep restoration rationale |
| `docs/proactive_signals/claudedevs_intel_v2_design.md` | doc | **X/Twitter** intel (Phases 0–5) — a SEPARATE pipeline, do not conflate with YouTube |

---

*Authored by a Claude Code investigation session, 2026-05-29. All claims code-verified at
authoring time; re-verify line numbers if the files have since changed.*
