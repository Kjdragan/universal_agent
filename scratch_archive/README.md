# Scratchpad artifact archive (per-project, durable)

This directory is the **standing, durable record of every tailnet-scratchpad exhibit
published interactively from this repo** — plans, triage lists, diff reviews, digests,
diagrams, any HTML/markdown/file handed to the operator via the scratchpad.

It is written automatically by `scripts/publish_scratch.sh` (which calls
`scripts/scratch_archive.py`) **after every successful publish**. You don't run anything by
hand — publish to the scratchpad as usual and a dated copy lands here.

## Why it exists

The live tailnet store (`/home/ua/ua_scratch/<slug>/`) is flat (one dir per slug, no dates,
no project grouping) and is the only copy. This archive adds a permanent, organized,
**per-project** second copy so we always have a browsable file of everything we made,
independent of `project_docs/`. **Nothing prunes it — retention is indefinite by design.**

## Layout

```
scratch_archive/
  index.jsonl                 append-only ledger (source of truth)
  INDEX.md                    human-readable, newest-first (open this)
  index.html                  browsable + searchable (same data, rendered)
  <YYYY-MM-DD>/
    <HHMMSS>__<slug>__<name>            a single-file exhibit
    <HHMMSS>__<slug>/<tree…>            a docset (multi-file) exhibit
```

## Where each kind of exhibit is archived

| Who published | Where it runs | Archive root |
|---|---|---|
| You + Claude, interactively | this repo on the desktop | **this dir** (`<repo>/scratch_archive/`, committed to git) |
| Autonomous runs (Simone, digests, …) | the VPS | `/home/ua/ua_scratch_archive/` (permanent, served at `/scratch-archive`, not git) |

This is universal: working in *any* repo, interactive exhibits archive into that repo's own
`scratch_archive/`. Outside any repo they fall back to `~/ua_scratch_archive/`.

## Knobs

- `UA_SCRATCH_ARCHIVE_ENABLED=0` — turn archiving off.
- `UA_SCRATCH_ARCHIVE_ROOT=<dir>` — override the archive root (tests pin this to a temp dir).

See `project_docs/06_platform/06_networking_tailscale_proxy_sshfs.md` § 1.6 for the full design.
