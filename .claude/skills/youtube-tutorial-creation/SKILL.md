---
name: youtube-tutorial-creation
description: >
  Convert a YouTube tutorial video into durable TEACHING-DOC learning artifacts stored
  under UA_ARTIFACTS_DIR: always CONCEPT.md + README.md + manifest.json (plus an optional
  procedural IMPLEMENTATION.md usage runbook). This skill NEVER builds runnable code:
  no implementation/ folder, no repo scaffold - runnable demos are built post-gate in
  /opt/ua_demos by the separate tutorial_build Task Hub lane. USE when a user provides a
  YouTube URL and wants to learn, understand, or deeply study a video, or when a
  webhook/hook payload contains a YouTube URL with a learning/tutorial intent.
  Trigger phrases: "create a tutorial from this video", "help me learn from this YouTube
  link", "turn this YouTube video into a guide", "make me study notes from this",
  "break down this video for me".
---

# YouTube Tutorial Creation Skill

Converts a YouTube tutorial into durable, re-usable learning artifacts. The output should
be understandable *without* watching the video.

> **Mandatory Dependency:** Always invoke the `youtube-transcript-metadata` skill first (Step 3).
> Never fetch transcripts or metadata inline — the transcript skill is the single source of truth.

> [!IMPORTANT]
> **Tier contract (P3) — TEACHING-DOC ONLY. No implementation builds, ever.**
> This skill must NEVER create an `implementation/` directory, a runnable code project,
> a `.venv`/`pyproject.toml` scaffold, or any repo bootstrap script. Your job is study
> material on how to USE the feature/capability as shown in the video. The runnable
> Demo (a standalone mini-app) is built post-gate in `/opt/ua_demos/<id>` by the
> separate `tutorial_build` Task Hub lane (Cody) - see
> `project_docs/04_intelligence/15_demo_tutorial_pipeline_adr.md`. Always set
> `"implementation_required": false` and `"learning_mode": "concept_only"` in
> `manifest.json`.

## Output artifacts

| File | Required | Purpose |
|------|----------|---------|
| `manifest.json` | ✅ Always | Provenance, status, retention map |
| `README.md` | ✅ Always | One-page summary with metadata context block |
| `CONCEPT.md` | ✅ Always | Standalone tutorial — understandable without watching the video |
| `IMPLEMENTATION.md` | ⬜ Recommended | Procedural usage runbook (how to USE the tool/feature) — never a code project |
| `visuals/zai_video_analysis.md` | ⬜ Best-effort for code-oriented videos | Timestamped visual analysis from ZAI Vision |
| `research/sources.md` | ⬜ When gaps exist | Gap-filling sources and citations |
| `transcript.clean.txt` | ⬜ Recommended | Deduplicated transcript (retention: temp) |

---

## Artifact Directory Convention

All durable outputs go under:

```
<resolved_artifacts_root>/youtube-tutorial-creation/{YYYY-MM-DD}/{video-slug}__{HHMMSS}/
```

> ⚠️ **Path hygiene**: Never put a literal `UA_ARTIFACTS_DIR` in a path.  
> BAD: `UA_ARTIFACTS_DIR/...` or `/opt/universal_agent/UA_ARTIFACTS_DIR/...`  
> GOOD: Resolve the root first (Step 2 below), then append `youtube-tutorial-creation/...`

---

## Workflow

### Step 1 — Confirm inputs

- If no URL provided, ask for one.
- Output is always the teaching-doc tier (`learning_mode=concept_only`); do not offer an implementation build.

### Step 2 — Preflight: resolve artifacts root (MANDATORY)

Never rely on Bash inheriting env vars. Resolve the artifacts root explicitly:

```bash
python3 -c "from universal_agent.artifacts import resolve_artifacts_dir; print(resolve_artifacts_dir())"
```

If this fails, **STOP** and report the error. Do not fall back to writing only under transient scratch.

Then:

- Compute `video-slug` from the video title (preferred) or URL video ID.
- Create the run directory under the resolved artifacts root.
- Start `manifest.json` immediately — fill fields as you go.

> **Tool preference:** Use `write_text_file` for all writes into `UA_ARTIFACTS_DIR`.
> Native `Write` may be restricted to the session workspace depending on runtime.

### Step 3 — Transcript + metadata ingestion (MANDATORY)

Run the core ingestion script with parallel transcript + metadata extraction:

```bash
UV_CACHE_DIR=/tmp/uv_cache uv run .claude/skills/youtube-transcript-metadata/scripts/fetch_youtube_transcript_metadata.py \
  --url "<YOUTUBE_URL>" \
  --language en \
  --json-out "$CURRENT_RUN_WORKSPACE/downloads/youtube_ingest.json" \
  --transcript-out "$CURRENT_RUN_WORKSPACE/downloads/transcript.txt" \
  --pretty
```

`CURRENT_RUN_WORKSPACE` is the canonical workspace variable. `CURRENT_SESSION_WORKSPACE`
may still be present as a legacy alias during migration, but new examples should prefer
the run workspace name.

Read `youtube_ingest.json` and look at `ok`, `failure_class`, `transcript_text`, and `metadata`.

**Degraded-mode decision tree:**

| Situation | Action |
|-----------|--------|
| `ok=true` | Continue to Step 3b (normal path) |
| `ok=false`, `failure_class=request_blocked` | Retry once; if still blocked, proceed degraded with metadata only |
| `ok=false`, `failure_class=empty_or_low_quality_transcript` | For code-oriented videos (`mode=explainer_plus_code`), use ZAI Vision analysis as supplemental evidence when available; otherwise continue with metadata-only degraded output |
| `ok=false`, metadata succeeded | Preserve metadata in manifest; proceed degraded |
| Both transcript and metadata failed | Set status `failed`; still write `manifest.json` + `README.md` with error detail |

Source-of-truth policy:

- Transcript: `YouTubeTranscriptApi().fetch(video_id)` — **never** `get_transcript()` (legacy)
- Metadata: `yt-dlp` only — **never** use yt-dlp for transcript text

### Step 3b — Transcript cleanup (recommended)

Caption transcripts often contain consecutive duplicate lines. Deduplicate:

```bash
python3 - <<'PY'
import os
from pathlib import Path

ws = Path(
    os.environ.get("CURRENT_RUN_WORKSPACE")
    or os.environ["CURRENT_SESSION_WORKSPACE"]
)
src = ws / "downloads" / "transcript.txt"
dst = ws / "downloads" / "transcript.clean.txt"

lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
out, prev = [], None
for line in lines:
    if line != prev:
        out.append(line)
    prev = line

dst.write_text("\n".join(out).strip() + "\n", encoding="utf-8")
print(f"Wrote {dst} ({len(out)} lines, from {len(lines)} original)")
PY
```

### Step 3c — Full-transcript pass (MANDATORY)

Do a full-file read of the cleaned transcript to avoid "cut-off" issues from partial previews,
and produce a stats snapshot for the manifest:

```bash
python3 - <<'PY'
import json, os
from pathlib import Path

ws = Path(
    os.environ.get("CURRENT_RUN_WORKSPACE")
    or os.environ["CURRENT_SESSION_WORKSPACE"]
)
src = ws / "downloads" / "transcript.clean.txt"
dst = ws / "downloads" / "transcript.stats.json"

text = src.read_text(encoding="utf-8", errors="replace")
lines = text.splitlines()
stats = {
    "path": str(src),
    "bytes": len(text.encode("utf-8", errors="replace")),
    "chars": len(text),
    "lines": len(lines),
    "head": lines[:5],
    "tail": lines[-5:],
}
dst.write_text(json.dumps(stats, indent=2), encoding="utf-8")
print(f"Wrote {dst} ({stats['lines']} lines, {stats['chars']} chars)")
PY
```

### Step 3d — Metadata handoff (MANDATORY)

Carry key fields from `youtube_ingest.json` into:

- `manifest.json`: `title`, `channel`, `duration`, `upload_date`, `description`, `metadata_status`, `metadata_source`
- `README.md`: context block at the top (URL, title, channel, duration, upload date)

### Step 3e — Description link analysis (MANDATORY when description exists)

The video description often contains links to source code repositories, datasets, competition pages,
and documentation that are **essential context** for building high-quality tutorials.

Run the automated link extraction script:

```bash
UV_CACHE_DIR=/tmp/uv_cache uv run \
  .claude/skills/youtube-tutorial-creation/scripts/extract_description_links.py \
  --ingest-json "$CURRENT_RUN_WORKSPACE/downloads/youtube_ingest.json" \
  --output-dir "$CURRENT_RUN_WORKSPACE/work_products/description_resources" \
  --report-json "$CURRENT_RUN_WORKSPACE/work_products/description_links_report.json" \
  --max-links 5 --timeout 10
```

The script automatically:
1. Reads `metadata.description` from `youtube_ingest.json`
2. Extracts and classifies all URLs into categories:
   - `github_repo` — GitHub/GitLab repository links (**shallow-cloned** for full source access)
   - `kaggle_competition` / `kaggle_dataset` — Kaggle pages (fetches overview content)
   - `documentation` — Official docs (extracts clean markdown)
   - `dataset` — Data sources like Hugging Face (records URL)
   - `social` — Social media / promo links (automatically filtered out)
3. Fetches content for high-value links using **direct connections** (no proxy)
4. Saves resources to `work_products/description_resources/`
5. Writes a structured report to `description_links_report.json`

> [!IMPORTANT]
> **Proxy rule:** The script fetches external links using **direct connections only**.
> Do NOT route these through the Webshare residential proxy.
> The proxy is ONLY for YouTube API calls (transcript + yt-dlp metadata).

After the script runs, read `description_links_report.json` and:
1. Copy the `links` array into `manifest.json` as `description_links`
2. Read any successfully fetched resources from `work_products/description_resources/`
3. Use these as **supplementary context** during synthesis (Step 5):
   - **Cloned GitHub repos** provide the full source code — read key files (README, main scripts,
     config files, pyproject.toml/requirements.txt) directly from the clone directory.
     The `REPO_INFO.md` inside each clone has metadata and a file listing.
   - Kaggle pages provide problem definitions and evaluation criteria
   - Documentation provides API references and usage patterns

If the script reports `status: skipped_no_description`, skip to Step 4.

**If description is empty or contains no useful links:** Skip this step and continue to Step 4.


### Step 4 — Visual analysis (best-effort, coding runs only)

Run optional video/vision analysis only for code-oriented videos (`mode=explainer_plus_code`).
For non-code runs, skip this step and set `extraction.visual = "not_attempted"` in the manifest.

When visual analysis is appropriate, use `mcp__zai_vision__video_analysis` when available to the agent runtime and save the result under:

```text
<run_dir>/visuals/zai_video_analysis.md
<run_dir>/visuals/zai_video_analysis.json
```

If ZAI Vision is unavailable, rate limited, or cannot analyze the video, **do not skip the whole skill**.
Set `extraction.visual = "attempted_failed"` in the manifest and continue with transcript-only mode.

### Step 5 — Synthesis

Merge "what they said" (transcript) with "what they showed" (visual findings, when a coding run attempted them):

- Identify gaps and ambiguities in the content
- Do supplementary research when needed (prefer official docs, then reputable sources)
- Record all gap-filling sources in `research/sources.md` with URLs and access dates

### Step 6 — Write durable artifacts

Write each artifact to the run directory. Quality bar for each:

**`README.md`**

- Video title, channel, URL, duration, upload date (from metadata)
- 2–3 sentence summary of what the video covers
- Links to `CONCEPT.md` and `IMPLEMENTATION.md`
- **Markdown Formatting**: Ensure a highly readable, premium layout. Use clear headings, ample spacing, bullet points, and a clean visual hierarchy so it is easy on the eyes when rendered in the UI. Specify visually calm typography elements where possible.

**`CONCEPT.md`** — standalone tutorial, no video required

- Introduction explaining *why* this technology/approach exists
- Core concepts with enough depth to understand the implementation
- Diagrams or references to `visuals/` when available
- Code snippets with provenance comments linking to source timestamps or `visuals/code-extractions/`
- Must be readable by someone who has never seen the video
- **Markdown Formatting**: Format beautifully for the UI panel. Provide breathing room (blank lines between blocks), use blockquotes for key takeaways, well-structured headers, and a calm, readable typographic flow.

**`IMPLEMENTATION.md`** — procedural usage runbook (teaching doc, NOT a code project)

- How to USE the feature/capability exactly as presented in the video (commands, configuration, UI/CLI workflow)
- Prerequisites (exact versions where known)
- Step-by-step instructions with expected outputs at each step
- Troubleshooting section for likely failure modes
- Inline code SNIPPETS with provenance are welcome; never write them out as a runnable project or `implementation/` directory

### Step 7 — Finalize manifest

Update `manifest.json` to its final state. See `references/output_contract.md` for the
full schema. Required fields:

```json
{
  "skill": "youtube-tutorial-creation",
  "status": "full | degraded_transcript_only | failed",
  "learning_mode": "concept_only",
  "implementation_required": false,
  "video_url": "...",
  "video_id": "...",
  "source": "manual | composio | direct",
  "metadata": { "title": "...", "channel": "...", "duration": 0, "upload_date": "...", "description": "...", "metadata_status": "...", "metadata_source": "yt_dlp" },
  "description_links": [
    { "url": "...", "type": "github_repo | kaggle_competition | documentation | dataset | other", "fetched": true, "resource_path": "..." }
  ],
  "extraction": {
    "transcript": "attempted_succeeded | attempted_failed | not_attempted",
    "metadata": "attempted_succeeded | attempted_failed | not_attempted",
    "visual": "attempted_succeeded | attempted_failed | not_attempted",
    "description_links": "attempted_succeeded | attempted_failed | not_attempted | skipped_no_links"
  },
  "outputs": { "CONCEPT.md": "...", "IMPLEMENTATION.md": "...", "manifest.json": "..." },
  "retention": { "transcript.txt": "temp", "transcript.clean.txt": "temp" },
  "notes": []
}
```

`learning_mode` is always `"concept_only"` and `implementation_required` is always
`false` — the legacy concept-plus-implementation value is retired (runnable demos
are built in `/opt/ua_demos` by the `tutorial_build` lane, not by this skill).

---

## Success Criteria

- All required artifacts live under the resolved `UA_ARTIFACTS_DIR` (never only in run scratch)
- `manifest.json` exists, is accurate, and has a valid `status`
- `CONCEPT.md` is understandable without watching the video
- NO `implementation/` directory exists in the run folder (teaching-doc only; the runnable demo lives in `/opt/ua_demos` via the `tutorial_build` lane)

---

## Reference files

Read these when you need deeper detail:

| File | When to read |
|------|-------------|
| `references/output_contract.md` | Full manifest schema, required vs optional files, status/mode values |
| `references/ingestion_and_tooling.md` | Tool selection decision matrix, runtime strategy |
| `references/composio_wiring_checklist.md` | Composio + webhook ingress validation |
