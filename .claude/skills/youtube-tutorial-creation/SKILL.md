---
name: youtube-tutorial-creation
description: >
  Convert a YouTube tutorial video into durable, referenceable learning artifacts stored
  under UA_ARTIFACTS_DIR. Always produces CONCEPT.md + manifest.json, and conditionally
  produces runnable implementation artifacts when the content is truly software/coding.
  USE when a user provides a YouTube URL and wants to learn, understand, implement from,
  or deeply study a video. Also trigger when a webhook/hook payload contains a YouTube URL
  with a learning/tutorial intent. Trigger phrases: "create a tutorial from this video",
  "help me learn from this YouTube link", "implement what's shown in this video",
  "turn this YouTube video into a guide", "make me study notes from this", "explain and
  implement this YouTube tutorial", "I want to implement this", "break down this video for me".
---

# YouTube Tutorial Creation Skill

Converts a YouTube tutorial into durable, re-usable learning artifacts. The output should
be understandable *without* watching the video.

> **Mandatory Dependency:** Always invoke the `youtube-transcript-metadata` skill first (Step 3).
> Never fetch transcripts or metadata inline — the transcript skill is the single source of truth.

## Output artifacts

| File | Required | Purpose |
|------|----------|---------|
| `manifest.json` | ✅ Always | Provenance, status, retention map |
| `README.md` | ✅ Always | One-page summary with metadata context block |
| `CONCEPT.md` | ✅ Always | Standalone tutorial — understandable without watching the video |
| `IMPLEMENTATION.md` | ✅ Usually | Prerequisites/steps; for concept-only this can be procedural (recipe/runbook) |
| `implementation/` | ⬜ Conditional | Runnable code/scripts only for software/coding tutorials |
| `visuals/gemini_video_analysis.md` | ⬜ Best-effort | Timestamped visual analysis from Gemini |
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
- Ask for the goal: **concept-only** vs **concept + implementation**.
- Ask for target language/framework if the video is ambiguous about it.

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
| `ok=false`, `failure_class=empty_or_low_quality_transcript` | Use Gemini visual analysis as primary source; set status `degraded_transcript_only` |
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

1. Read `metadata.description` from `youtube_ingest.json`
2. If the description is non-empty, extract all URLs using regex or url-parsing
3. Classify each URL into one of these categories:
   - `github_repo` — GitHub/GitLab repository links
   - `kaggle_competition` or `kaggle_dataset` — Kaggle competition or dataset pages
   - `documentation` — Official documentation links (readthedocs, docs sites)
   - `dataset` — Data download links (CSV, Hugging Face, etc.)
   - `social` — Social media, promotional, or affiliate links (discard)
   - `other` — Unclassified but potentially useful
4. For high-value links (github_repo, kaggle_*, documentation, dataset), fetch their content:

> [!IMPORTANT]
> **Proxy rule:** Fetch external description links using **direct connections only**.
> Do NOT route these through the Webshare residential proxy.
> The proxy is ONLY for YouTube API calls (transcript + yt-dlp metadata).

   - **GitHub repos:** Fetch README.md and file tree (do not clone entire repo)
   - **Kaggle:** Fetch competition description and dataset info page
   - **Documentation:** Use `defuddle` or `read_url_content` for clean markdown extraction
   - **Datasets:** Note the download URL; do not download large files
5. Save fetched content to `$CURRENT_RUN_WORKSPACE/work_products/description_resources/`
6. Record each link and its fetch status in the manifest:

```json
{
  "description_links": [
    {
      "url": "https://github.com/user/repo",
      "type": "github_repo",
      "fetched": true,
      "resource_path": "work_products/description_resources/repo_readme.md"
    },
    {
      "url": "https://www.kaggle.com/competitions/...",
      "type": "kaggle_competition",
      "fetched": true,
      "resource_path": "work_products/description_resources/kaggle_page.md"
    }
  ]
}
```

7. Use fetched resources as supplementary context during CONCEPT.md and IMPLEMENTATION.md synthesis (Step 5)
8. Cap at **5 high-value links** to avoid excessive fetching
9. Set a **10-second timeout** per link fetch; record failures rather than blocking the pipeline

**If description is empty or contains no useful links:** Skip this step and continue to Step 4.

### Step 4 — Visual analysis (best-effort)

Attempt Gemini multimodal analysis against the YouTube URL:

```bash
UV_CACHE_DIR=/tmp/uv_cache uv run .claude/skills/youtube-tutorial-creation/scripts/gemini_video_analysis.py \
  --url "<YOUTUBE_URL>" \
  --out "<run_dir>/visuals/gemini_video_analysis.md" \
  --json-out "<run_dir>/visuals/gemini_video_analysis.json"
```

If the script fails (no API key, model unavailable, rate limited), **do not skip the whole skill**.
Set `extraction.visual = "attempted_failed"` in the manifest and continue with transcript only.

> Do NOT skip visual analysis just because you *assume* the transcript is sufficient.
> Attempt it, record the result, and proceed.

Self-test (verifies imports only, no API call):

```bash
UV_CACHE_DIR=/tmp/uv_cache uv run .claude/skills/youtube-tutorial-creation/scripts/gemini_video_analysis.py --self-test
```

### Step 5 — Synthesis

Merge "what they said" (transcript) with "what they showed" (visual findings):

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

**`IMPLEMENTATION.md`** — practical runbook (or procedural guide for concept-only)

- Prerequisites (exact versions where known)
- Step-by-step instructions with expected outputs at each step
- Troubleshooting section for likely failure modes
- References to `implementation/` scripts

**`implementation/`** — runnable code (only when `learning_mode=concept_plus_implementation`)

- Use uv inline scripting (PEP 723) for all Python scripts (see Step 8)
- Add comments with provenance (timestamp reference or visual extraction source)
- Store raw OCR code extractions with confidence headers in `visuals/code-extractions/`

### Step 7 — Finalize manifest

Update `manifest.json` to its final state. See `references/output_contract.md` for the
full schema. Required fields:

```json
{
  "skill": "youtube-tutorial-creation",
  "status": "full | degraded_transcript_only | failed",
  "learning_mode": "concept_only | concept_plus_implementation",
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

### Step 8 — Implementation validation (MANDATORY)

Every Python script in `implementation/` must be runnable without a separate venv.

Requirements:

1. Include PEP 723 inline dependency metadata at the top:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-genai>=1.0.0", "python-dotenv>=1.0.1"]
# ///
```

1. Add a `--self-test` flag that checks imports + basic object construction **without** requiring secrets.
2. Load secrets from environment only (never hardcode), using `load_dotenv(find_dotenv(usecwd=True))`.
3. Valid secret env var names: `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `Z_AI_API_KEY`, `ANTHROPIC_API_KEY`.
4. Validate with: `UV_CACHE_DIR=/tmp/uv_cache uv run implementation/<script>.py --self-test`

> **Hard rules:**  
> Do NOT run `pip install`, `uv pip install`, or `uv add` as part of a skill run.  
> If deps are missing, fix the PEP 723 header and re-run via `uv run`.

**SDK drift note:** Prefer `google.genai` for all new code. If the tutorial video uses
`google.generativeai`, note it as "video used legacy SDK" and implement with the current SDK.

---

## Success Criteria

- All required artifacts live under the resolved `UA_ARTIFACTS_DIR` (never only in run scratch)
- `manifest.json` exists, is accurate, and has a valid `status`
- `CONCEPT.md` is understandable without watching the video
- `implementation/` scripts pass `--self-test` (or has clear documented reason if not applicable)

---

## Reference files

Read these when you need deeper detail:

| File | When to read |
|------|-------------|
| `references/output_contract.md` | Full manifest schema, required vs optional files, status/mode values |
| `references/ingestion_and_tooling.md` | Tool selection decision matrix, runtime strategy |
| `references/composio_wiring_checklist.md` | Composio + webhook ingress validation |
