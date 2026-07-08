# Handoff — BUILD: add an optional X (Twitter) lane to `analyze-google-trends`

**For the next session — this is an implementation task, not research.** The decision is made; execute it.
Written 2026-06-27 (desktop, Opus 4.8 / Max). Background/why is in the companion docs (don't re-litigate):
- Decision + risk analysis: `/tmp/handoff_x_access_investigation.md`
- The skill being extended + its origin: `/tmp/handoff_trends_demo_original.md` (scratchpad:
  https://uaonvps.taildcc090.ts.net/scratch/trends-demo-handoff/handoff_trends_demo_original.md)

## Objective (the agreed plan, verbatim)
> (1) bump Node to 22, (2) vendor the MIT `bird-search/` + write `research_x.py` + wire it as the optional
> X lane with graceful degrade, (3) verify with a `--check`, then a real search once the two cookies are
> pasted. Cookies aren't needed until the final verify step.

Add X as a **fourth, optional source** to the skill's conversation signal (alongside HN + Reddit + web),
reusing last30days' **self-healing** vendored `bird-search` (cookie auth → X GraphQL `SearchTimeline`).
We write only thin glue; we own **no** X-protocol code.

## Preconditions
- **Node ≥ 22.** Box is on **v20.12.2**; bird's `package.json` declares `engines: node>=22` (it *ran* on 20
  with an experimental-JSON-import warning, but bump to be safe). First check how node is installed
  (`which node`, nvm vs system) and bump accordingly (e.g. `nvm install 22 && nvm alias default 22`).
- **Cookies — only at the final verify step.** Manual `auth_token` + `ct0` from a logged-in x.com session
  (DevTools → Application → Cookies → x.com). Auto-extract is NOT available (needs uninstalled
  `@steipete/sweet-cookie`), so manual is the path. **Use a secondary X account.**

## Build steps

### 1. Vendor `bird-search/` into the skill (MIT — keep LICENSE + attribution)
- Source (verified self-contained, Node-only, self-healing query IDs):
  `~/lrepos/universal_agent/.claude/skills/last30days/scripts/lib/vendor/bird-search/`
- Copy → `~/lrepos/demo-trends-to-sheets-agent/skill/vendor/bird-search/` (the skill dir is the
  source of truth; `promote_skill.py` copies the whole dir, so `vendor/` travels into dragan-plugins —
  this is why we vendor rather than reference last30days at runtime: portability).
- Preserve `LICENSE` + the `attribution` field in its `package.json`. Confirm the **search path has no
  npm deps** (the `@steipete/sweet-cookie` dep is only for browser auto-extract, which we don't use).
- CLI contract (verified): `node bird-search.mjs "<query>" [--count N] [--json]`, plus `--check` /
  `--whoami`. Reads creds from `AUTH_TOKEN`/`CT0` env or `--auth-token`/`--ct0` flags. Self-heals query
  IDs via `lib/runtime-query-ids.js` (fetches X's live JS bundles, 24h cache in `~/query-ids-cache.json`).

### 2. Write `skill/scripts/research_x.py` — mirror `research_topic.py`
`research_topic.py` (same dir) is the template — match its shape exactly: argparse, `--selftest`,
deterministic `rank_score`, per-source list + merged `ranked`, graceful degrade, stdlib for everything
except the node subprocess. Spec:
- Flags: `--topic` (required), `--window day|week|month` (reuse the same `WINDOW={day:1,week:7,month:30}`
  map as `research_topic.py`), `--limit`, `--json`, `--selftest`.
- Locate bird: `Path(__file__).parents[1] / "vendor" / "bird-search" / "bird-search.mjs"`.
- Build the X query from topic + window: append `since:<YYYY-MM-DD>` computed from `time.time() -
  days*86400` (a standalone script MAY use `time.time()`). Optionally `min_faves:` to bias to engagement.
- Run: `subprocess` → `node <mjs> "<query>" --count <limit> --json`, with cookies passed via
  `env={**os.environ, "AUTH_TOKEN":..., "CT0":...}` (read from `X_AUTH_TOKEN`/`X_CT0`, fall back to
  `AUTH_TOKEN`/`CT0`). Parse stdout JSON → map each tweet to the **same item shape** as research_topic:
  `{source:"x", title:<text>, url, engagement:(likes+reposts), comments:<replies>, age_days, when,
  score:rank_score(...)}`.
- **Graceful degrade (never crash):** node missing → `{"counts":{"x":0},"note":"node unavailable"}`;
  cookies absent → `note:"x skipped — no cookies"`; bird returns `authenticated:false` or an HTML
  anti-bot interstitial (non-JSON stdout) → `note:"x unavailable — auth/anti-bot"`. Mirror
  research_topic's try/except-to-empty pattern.
- `--selftest` (offline, no network/node): test the query construction (window→`since:` date) and the
  tweet→item mapping from a JSON fixture; reuse/assert `rank_score`. Must print `SELFTEST: PASS`.

### 3. Wire it into `SKILL.md`
- In the DEEP/COMPARE fetch step, add X as an optional source after HN/Reddit/web:
  `python3 scripts/research_x.py --topic "<t>" --window <w> --json` (gated on cookies; skip silently if
  absent). Add `🐦 X: <n> posts` (or `X: skipped — no cookies`) to the "sources reported back" footer.
- Add a short **"X lane (optional, cookie auth)"** note: how to set `X_AUTH_TOKEN`/`X_CT0`, the
  desktop-only/secondary-account/ToS caveat, and that it degrades gracefully when unset. Keep the keyless
  tier (Trends+HN+Reddit+web) as the default that works with zero setup.

### 4. Secret hygiene (enforce)
- Cookies go in env or a **gitignored** local file (the demo repo's `.env` is already gitignored /
  Infisical-bootstrapped) — **never** committed, logged, or echoed into a transcript.
- `.gitignore`: commit `skill/vendor/**` (our vendored code) but ensure `~/query-ids-cache.json` (written
  to home, not the repo) and any cookie/.env file are NOT committed.

## Verification (in order)
1. `python3 skill/scripts/research_x.py --selftest` → `SELFTEST: PASS` (offline).
2. `python3 skill/scripts/research_topic.py --selftest` + `fetch_trends.py --selftest` still pass (no regression).
3. `node skill/vendor/bird-search/bird-search.mjs --check` → confirms the creds path runs (will report
   `authenticated:false` until cookies are set — that's expected).
4. **Final, once the operator pastes cookies:** set `X_AUTH_TOKEN`/`X_CT0`, run a real
   `research_x.py --topic "claude code" --window week` → expect ranked X posts with engagement, and a
   DEEP-mode briefing that now includes the 🐦 X block.
5. Ship: commit on branch `robust-auto-merge` (the skill's current branch — see origin handoff), push,
   then re-promote: `python3 ~/lrepos/demo_factory/scripts/promote_skill.py --skill-dir
   ~/lrepos/demo-trends-to-sheets-agent/skill --demo-id trends-to-sheets-agent` → dragan-plugins PR →
   merge. Confirm `/dragan:analyze-google-trends` carries `research_x.py` + `vendor/`.

## Hard rules / risks (non-negotiable)
- **Desktop-only. NEVER on the VPS or in autonomous/cron loops** — both a ToS escalation and a
  credential-exposure risk.
- `auth_token` is a **full account credential** (can post/DM as the user) — treat like a password.
- ToS: low request volume, secondary account. If X changes its JS-bundle *format*, even the self-heal
  breaks until the vendored `bird-search` is re-synced from upstream (`last30days` repo) — accept that
  maintenance model; do not reimplement the protocol.

## Suggested skills
- **`dependency-management`** — for the Node ≥22 bump (uv/system/nvm), done cleanly.
- **`read-the-damn-docs`** / **`deepwiki`** — confirm bird's CLI flags + X search-operator syntax
  (`since:`/`min_faves:`) against the vendored source / `mvanhorn/last30days-skill` before coding.
- **`verification-before-completion`** — enforce the verify sequence before claiming done.
- **`security-and-hardening`** — sanity-check the cookie/secret handling.
- **`publish-to-scratchpad`** — surface the result/briefing as a rendered link for the terminal-only operator.

## Sensitive data
None embedded. The build introduces `X_AUTH_TOKEN` / `X_CT0` (cookie values) — **names only here**; values
are pasted at verify time into env / a gitignored file and must never be committed, logged, or echoed.
