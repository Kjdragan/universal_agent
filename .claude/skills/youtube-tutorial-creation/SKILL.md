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

**`implementation/`** — runnable, production-ready project (only when `learning_mode=concept_plus_implementation`)

The `implementation/` directory should be a **fully set up, ready-to-run repository** — not just loose scripts.
The user should be able to `cd implementation/ && uv sync && uv run main.py` immediately.

Required structure:

```
implementation/
├── pyproject.toml          # UV-managed deps (see Step 8)
├── README.md               # Project-specific usage instructions
├── main.py                 # Primary entry point (or appropriate name)
├── load_env.py             # Infisical/env helper (see below)
├── .env.example            # Template for bootstrap credentials only
├── docs/                   # Documentation skeleton
│   ├── README.md           # Thematic index
│   └── Documentation_Status.md  # Status tracker
└── <additional source files as needed>
```

#### Environment & Dependency Setup (MANDATORY)

1. **`pyproject.toml`** — Declare all dependencies via UV:
   ```toml
   [project]
   name = "<project-name>"
   version = "0.1.0"
   requires-python = ">=3.11"
   dependencies = ["google-genai>=1.0.0"]
   ```
   The agent must run `uv sync` after creating the project to ensure the `.venv` is ready.

2. **`load_env.py`** — Infisical-first secret loading helper:
   ```python
   import os
   def load_env():
       """Load secrets from Infisical, falling back to os.environ."""
       try:
           from infisical_sdk import InfisicalClient
           client = InfisicalClient()
           secret = client.get_secret("GEMINI_API_KEY")
           if secret and secret.secret_value:
               os.environ["GEMINI_API_KEY"] = secret.secret_value
               return
       except Exception:
           pass
       if "GEMINI_API_KEY" not in os.environ:
           print("Warning: GEMINI_API_KEY not found. Use: infisical run -- uv run main.py")
   ```

3. **`.env.example`** — Bootstrap template only (NOT secrets):
   ```bash
   # Infisical bootstrap (fill in for production/VPS deployment)
   INFISICAL_CLIENT_ID=""
   INFISICAL_CLIENT_SECRET=""
   INFISICAL_PROJECT_ID="9970e5b7-d48a-4ed8-a8af-43e923e67572"
   INFISICAL_ENVIRONMENT="production"
   ```

4. **`docs/`** — **MANDATORY: Seed the documentation skeleton with permanent knowledge.**
   The generated repo MUST be self-contained. Read the following reference files from this
   skill and write their full contents into the `implementation/docs/` directory:

   | Source (read from skill) | Destination (write into implementation) |
   |--------------------------|----------------------------------------|
   | `references/documentation_pattern.md` | `docs/documentation_pattern.md` |
   | `references/infisical_integration.md` | `docs/infisical_integration.md` |
   | `references/vps_setup_guide.md` | `docs/vps_setup_guide.md` |

   Additionally create these index files:
   - `docs/README.md` — Thematic index linking to all docs above + any project-specific docs
   - `docs/Documentation_Status.md` — Status tracker listing each doc with last-updated date

   > [!IMPORTANT]
   > These are NOT optional references. The agent MUST `read` each file from the skill's
   > `references/` directory and `write` its complete contents into the implementation's
   > `docs/` directory. The resulting repo must work as standalone knowledge even if the
   > Universal Agent skill directory is unavailable.

5. **Running instructions in `README.md`**:
   ```markdown
   ## Quick Start
   ```bash
   uv sync                           # Install dependencies
   infisical run -- uv run main.py   # Run with secrets injected
   ```

   ## VPS Deployment
   See `docs/vps_setup_guide.md` for systemd service setup.
   ```

#### Code Quality
- Use uv inline scripting (PEP 723) for standalone scripts (see Step 8)
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
| `references/infisical_integration.md` | **NEW** — Secrets management patterns, `load_env.py` helper, CLI usage |
| `references/vps_setup_guide.md` | **NEW** — VPS specs, systemd setup, Nginx, ports, CI/CD deploy pattern |
| `references/documentation_pattern.md` | **NEW** — Dual-index doc system, agent documentation rules |
