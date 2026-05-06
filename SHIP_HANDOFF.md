# SHIP_HANDOFF

**Summary of changes made:**
YouTube daily digest: retry transcript fetch on residential-proxy bad-IP, plus richer failure logging.

**What ships:**
- `_run_youtube_transcript_api_extract` now retries up to 3 times through the proxy on transient failures (controlled by `UA_YOUTUBE_TRANSCRIPT_PROXY_RETRIES`, default 3). Each retry opens a new connection and gets a fresh egress IP from the rotating residential pool, so a single bad IP no longer poisons the whole fetch.
- The old "fall back to no-proxy" behavior is removed by default — from a VPS datacenter IP it's a guaranteed wasted attempt that obscures the real failure. Re-enable for local residential testing via `UA_YOUTUBE_TRANSCRIPT_NOPROXY_FALLBACK=1`.
- `youtube_daily_digest` now logs `failure_class` and `detail` on every transcript failure, not just the generic `error` name. Future post-mortems won't need a separate probe to figure out why a fetch failed.
- Two new unit tests in `tests/unit/test_youtube_ingest.py` pin the retry loop and the no-fallback default.

**Expected impact:** with default N=3 retries and an empirical ~20% bad-IP rate on Webshare/DataImpulse pools, single-video failure probability drops from ~20% to ~0.8%. On an 18-video playlist, mean failures per run drop from ~3.6 to ~0.14.

**Latest commits ready for /ship (in order):**
- `6dc6f51` — fix(youtube-digest): retry transcript fetch on residential-proxy bad-IP
- `05021fe` — docs: handoff note for transcript proxy-retry fix
- `20bf032` — test(youtube-ingest): un-stale 3 require_proxy tests against PROXY_PROVIDER router

**Post-deploy smoke test:**
1. SSH to VPS and run a manual digest dry-run against a populated day:
   ```
   ssh ua@uaonvps 'cd /opt/universal_agent && PYTHONPATH=src uv run python -m universal_agent.scripts.youtube_daily_digest --day TUESDAY --dry-run'
   ```
2. Compare ingestion failure count vs. last run — should be near zero on videos that have transcripts at all. Videos that legitimately lack captions will still go metadata-only (correct behavior).
3. The next normal 6 AM Central cron tick will exercise the new retry logic against whichever day's playlist has fresh content.

**Known risks:**
- A pathological all-bad-IP pool would now take up to 3× the time per failed video before giving up. Bounded by per-script timeout; not expected in practice.
- The removed no-proxy fallback was effectively a no-op on the VPS anyway (datacenter IPs are blocked), so nothing observable changes for production. Local-residential testers who relied on the implicit fallback should set `UA_YOUTUBE_TRANSCRIPT_NOPROXY_FALLBACK=1`.

**Pre-existing test failures (now FIXED in 20bf032):**
- `test_require_proxy_blocks_when_no_credentials`
- `test_require_proxy_blocks_when_module_unavailable`
- `test_require_proxy_true_proceeds_with_valid_proxy`

All three were stale (not deprecated): written before `_build_proxy_config` was added as the provider router. They patched/asserted Webshare-only behavior; production now defaults to DataImpulse with Webshare as alternative. Updated to either parametrize over both providers or patch the router entry point. `tests/unit/test_youtube_ingest.py` is now 24/24 green.

---

**Earlier in this branch (already merged via /ship Run #98):**
- `cc14d94` — feat(csi): add 22:00 Central poll to ClaudeDevs intel cron
- `20d58fe` — docs: handoff note for 22:00 cron schedule change
