---
name: youtube-tutorial-creation
description: |
  Turn a YouTube tutorial into durable learning artifacts (concept doc + runnable implementation) stored under UA_ARTIFACTS_DIR.
  USE WHEN user provides a YouTube URL and wants to learn/implement from it.
---

# YouTube Tutorial Creation Skill

> **Mandatory Dependency:** Always invoke the `youtube-transcript-metadata` skill first (Step 3 below).
> Never fetch transcripts or metadata inline — the transcript skill is the single source of truth.

This skill converts a YouTube tutorial into durable, referenceable artifacts:
- `CONCEPT.md` (educational tutorial-style writeup)
- `IMPLEMENTATION.md` (how to run/use)
- `implementation/` (runnable code/scripts/config)
- `visuals/` (key frames, OCR/code extractions, diagrams) when possible
- `research/` (gap-filling sources + citations)
- `manifest.json` (provenance + retention)

## Output Policy (MANDATORY)

### Persistent artifacts (default)
Write durable deliverables to:
- `UA_ARTIFACTS_DIR` (env var; injected by UA)

### Ephemeral scratch (only for intermediates)
Use:
- `CURRENT_SESSION_WORKSPACE` (env var; session directory) for downloads/caches/intermediate steps

## Artifact Directory Convention

Create a new run folder:
`UA_ARTIFACTS_DIR/youtube-tutorial-creation/{YYYY-MM-DD}/{video-slug}__{HHMMSS}/`

Inside it, write at minimum:
- `manifest.json`
- `README.md`
- `CONCEPT.md`
- `IMPLEMENTATION.md`
- `implementation/`
- `research/`
- `visuals/` (if any visual analysis performed)

## References / Support Assets

Use these companion docs for consistent ingress, tooling, and output quality:

1. `../youtube-transcript-metadata/SKILL.md` (core transcript+metadata ingestion)
2. `references/ingestion_and_tooling.md`
3. `references/composio_wiring_checklist.md`
4. `references/output_contract.md`

## Workflow

1. Confirm inputs
- If the user did not provide a URL, ask for one.
- Ask for the goal: concept-only vs concept+implementation.
- Ask for target language/framework if ambiguous.

0. Preflight (MANDATORY)
- Never rely on Bash inheriting env vars. Always be able to resolve paths yourself.
- Resolve artifacts root (absolute) in a way that works even if `UA_ARTIFACTS_DIR` is unset in the Bash environment:
  - `python3 -c "from universal_agent.artifacts import resolve_artifacts_dir; print(resolve_artifacts_dir())"`
- If you cannot resolve or create the artifacts root, STOP and report the error (do not fall back to writing “artifacts” under the session workspace).

2. Create artifact run dir (first)
- Compute `video-slug` from video title (preferred) or URL id.
- Create the run dir under `UA_ARTIFACTS_DIR` using the convention above.
- Start `manifest.json` immediately (fill fields as you go).
IMPORTANT: Prefer the `mcp__internal__write_text_file` tool for writing into `UA_ARTIFACTS_DIR`.
Native `Write` may be restricted to the session workspace depending on runtime.
Do NOT `cp -r` the entire session directory into artifacts; only write/copy the files that belong in the artifact package.
HARD RULE: Durable outputs must NOT be written under `CURRENT_SESSION_WORKSPACE/artifacts/...`. If `mcp__internal__write_text_file` is unavailable/denied, STOP and report (don’t silently fall back).
HARD RULE: Never treat `UA_ARTIFACTS_DIR` as a literal directory name in paths.
- BAD: `/opt/universal_agent/UA_ARTIFACTS_DIR/...`
- BAD: `UA_ARTIFACTS_DIR/...`
- GOOD: `<resolved_artifacts_root>/youtube-tutorial-creation/...`

3. Transcript + metadata ingestion (MANDATORY)
Use the core skill script so transcript and metadata are fetched in parallel:
- Script: `.claude/skills/youtube-transcript-metadata/scripts/fetch_youtube_transcript_metadata.py`
- Scratch outputs:
  - `CURRENT_SESSION_WORKSPACE/downloads/youtube_ingest.json`
  - `CURRENT_SESSION_WORKSPACE/downloads/transcript.txt`

```bash
uv run .claude/skills/youtube-transcript-metadata/scripts/fetch_youtube_transcript_metadata.py \
  --url "<YOUTUBE_URL>" \
  --language en \
  --json-out "$CURRENT_SESSION_WORKSPACE/downloads/youtube_ingest.json" \
  --transcript-out "$CURRENT_SESSION_WORKSPACE/downloads/transcript.txt" \
  --pretty
```

Source-of-truth policy:
- Transcript: `youtube-transcript-api` instance API (`YouTubeTranscriptApi().fetch(...)`)
- Metadata: `yt-dlp` (allowed and expected)
- Never use `yt-dlp` as transcript source.
- Never use legacy `YouTubeTranscriptApi.get_transcript(...)`.

Anti-blocking hygiene (mandatory):
- Keep transcript requests idempotent and dedupe by video id.
- Persist failure classes (`request_blocked`, `api_unavailable`, `empty_or_low_quality_transcript`) in run metadata.
- Enforce a minimum transcript character threshold before treating extraction as success.
- If transcript fails but metadata succeeds, still preserve metadata in manifest and docs.

3b. Transcript cleanup (HIGHLY RECOMMENDED)
Caption transcripts often include heavy duplication. After creating `downloads/transcript.txt`, dedupe consecutive identical lines and write:
- Scratch: `CURRENT_SESSION_WORKSPACE/downloads/transcript.clean.txt`
- Artifact: `<run_dir>/transcript.clean.txt` (typically `retention: temp`)

```bash
python3 - <<'PY'
import os
from pathlib import Path

ws = Path(os.environ["CURRENT_SESSION_WORKSPACE"])
src = ws / "downloads" / "transcript.txt"
dst = ws / "downloads" / "transcript.clean.txt"

lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
out = []
prev = None
for line in lines:
  if line == prev:
    continue
  out.append(line)
  prev = line

dst.write_text("\\n".join(out).strip() + "\\n", encoding="utf-8")
print(f"Wrote {dst} ({len(out)} lines, from {len(lines)} lines)")
PY
```

3c. Full-transcript pass (MANDATORY)
To avoid “cut off” problems from partial `Read` previews, always do at least one full-file pass over the cleaned transcript before synthesizing docs.
This also verifies the transcript is readable end-to-end.

- Input: `CURRENT_SESSION_WORKSPACE/downloads/transcript.clean.txt`
- Output: `CURRENT_SESSION_WORKSPACE/downloads/transcript.stats.json`

```bash
python3 - <<'PY'
import json
import os
from pathlib import Path

ws = Path(os.environ["CURRENT_SESSION_WORKSPACE"])
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
print(f"Wrote {dst} ({stats['lines']} lines)")
PY
```

3d. Metadata handoff (MANDATORY)
Read `downloads/youtube_ingest.json` and carry key metadata into:
- `manifest.json` (`title`, `channel`, `duration`, `upload_date`, `metadata_status`, `metadata_source`)
- `README.md` context block

4. Visual analysis (best effort)
Use Gemini multimodal understanding against the YouTube URL (preferred model: `gemini-3-pro-preview`):
- Script path: `.claude/skills/youtube-tutorial-creation/scripts/gemini_video_analysis.py`
- Output target: `<run_dir>/visuals/gemini_video_analysis.md`
- Include timestamped findings when possible and separate visual-only observations from transcript-derived claims.

If vision tooling is unavailable OR fails, proceed transcript-only and record the limitation in the manifest + docs.
Do NOT skip vision analysis just because you *assume* the transcript is sufficient.

5. Synthesis
Merge “what they said” (transcript) and “what they showed” (visual findings):
- Identify gaps/ambiguities
- Do supplementary research as needed (prefer official docs, then reputable sources)
- Record all gap-filling sources in `research/sources.md`

6. Write durable artifacts
- `CONCEPT.md`: standalone tutorial, includes diagrams/images (or references in `visuals/`) and carefully sourced code snippets.
- `IMPLEMENTATION.md`: prerequisites, steps, expected outputs.
- `implementation/`: runnable, cleaned code. Add comments with provenance + references to `visuals/code-extractions/` when relevant.
- `visuals/code-extractions/`: store raw OCR extractions with confidence headers (high/medium/low) and "COMPLETE/VALIDATED" flags.

7. Finish and finalize manifest
Update `manifest.json` with:
- inputs, extraction status, outputs map, tags
- retention map (mark safe-to-delete items as `temp`)
For each extraction step (transcript, visual), set an explicit status:
- `not_attempted` (only if tools are unavailable)
- `attempted_succeeded`
- `attempted_failed` (include the error and fallback)

8. Implementation validation (MANDATORY)
When you generate a Python sample script in `implementation/`, it MUST be runnable without a separate venv/pyproject.
Use uv inline scripting (PEP 723) and validate the script executes.

Requirements:
- Put dependency metadata at the top of the script:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["<dep1>", "<dep2>"]
# ///
```

- Add a `--self-test` mode that does imports + basic object construction WITHOUT requiring secrets.
- Validate with:
`uv run implementation/<script>.py --self-test`

HARD RULE: Do NOT run `pip install`, `uv pip install`, or `uv add` as part of the skill run.
If dependencies are missing, fix the PEP 723 header and re-run using `uv run`.

SDK drift note (MANDATORY):
- Prefer `google.genai` for new code (it is current). If the video uses `google.generativeai`, document that as “video used legacy SDK”, but implement with the current SDK and verify it imports with `uv run`.

Secrets policy (MANDATORY):
- Never write API keys or secrets into artifacts (no hardcoded strings in scripts, no keys in docs).
- Make scripts "just work" by auto-loading a `.env` file at runtime:
  - Add `python-dotenv` to PEP 723 deps.
  - Call `load_dotenv(find_dotenv(usecwd=True))` near the top of the script.
  - Read secrets only from environment variables (e.g., `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `Z_AI_API_KEY`, `ANTHROPIC_API_KEY`).

Suggested implementation pattern (google.genai + YouTube URL multimodal):

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-genai>=1.0.0", "python-dotenv>=1.0.1"]
# ///

from __future__ import annotations

import os
from google import genai
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

def _api_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

def build_client() -> genai.Client:
    key = _api_key()
    if not key:
        raise RuntimeError("Missing GOOGLE_API_KEY (or GEMINI_API_KEY)")
    return genai.Client(api_key=key)

def analyze_video(url: str, prompt: str) -> str:
    client = build_client()
    contents = [
        {
            "role": "user",
            "parts": [
                {"file_data": {"file_uri": url}},
                {"text": prompt},
            ],
        }
    ]
    resp = client.models.generate_content(
        model="gemini-3-pro-preview",
        contents=contents,
    )
    return resp.text or ""
```

Reference implementation to run directly:
- `uv run .claude/skills/youtube-tutorial-creation/scripts/gemini_video_analysis.py --self-test`
- `uv run .claude/skills/youtube-tutorial-creation/scripts/gemini_video_analysis.py --url "<youtube_url>" --out "<run_dir>/visuals/gemini_video_analysis.md"`

## Retention (Recommended Defaults)

In `manifest.json`:
- default: `keep`
- `transcript.txt`: `temp`
- `video-segments/`: `temp` (if created)
- large raw dumps/caches: `temp`

These `temp` items are safe to delete later via a cleanup command.

## Success Criteria

- Artifacts live under `UA_ARTIFACTS_DIR/...` (never only in session scratch)
- `manifest.json` exists and is accurate
- `CONCEPT.md` is understandable without watching the video
- `implementation/` is runnable or clearly documents what is missing and why
