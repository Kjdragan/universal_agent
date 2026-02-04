---
name: youtube-tutorial-learning
description: |
  Turn a YouTube tutorial into durable learning artifacts (concept doc + runnable implementation) stored under UA_ARTIFACTS_DIR.
  USE WHEN user provides a YouTube URL and wants to learn/implement from it.
---

# YouTube Tutorial Learning Skill

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
`UA_ARTIFACTS_DIR/youtube-tutorial-learning/{YYYY-MM-DD}/{video-slug}__{HHMMSS}/`

Inside it, write at minimum:
- `manifest.json`
- `README.md`
- `CONCEPT.md`
- `IMPLEMENTATION.md`
- `implementation/`
- `research/`
- `visuals/` (if any visual analysis performed)

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

3. Transcript extraction (best effort)
Preferred: use YouTube captions via `yt-dlp` into session scratch, then copy the final transcript into artifacts:
- Scratch: `CURRENT_SESSION_WORKSPACE/downloads/`
- Artifact: `<run_dir>/transcript.txt` (typically `retention: temp`)

Example (scratch):
`yt-dlp --skip-download --write-auto-subs --sub-lang en --convert-subs srt -o "<scratch>/subs.%(ext)s" "<URL>"`

Conversion helper (scratch → scratch):

```bash
python3 - <<'PY'
import os
import re
from pathlib import Path

ws = Path(os.environ["CURRENT_SESSION_WORKSPACE"])
downloads = ws / "downloads"

srt_files = sorted(downloads.glob("*.srt"))
if not srt_files:
  raise SystemExit(f"No .srt files found in {downloads}")

srt_path = srt_files[0]
out_path = downloads / "transcript.txt"

content = srt_path.read_text(encoding="utf-8", errors="replace")
lines = content.splitlines()

cleaned = []
for line in lines:
  s = line.strip()
  if not s:
    continue
  if s.isdigit():
    continue
  if re.match(r"^\\d{2}:\\d{2}:\\d{2},\\d{3}\\s*-->\\s*\\d{2}:\\d{2}:\\d{2},\\d{3}", s):
    continue
  cleaned.append(s)

out_path.write_text(\"\\n\".join(cleaned), encoding=\"utf-8\")
print(f\"Wrote {out_path} from {srt_path} ({len(cleaned)} lines)\")
PY
```

When converting SRT/VTT to plain text, use `python3` (not `python`), because some environments do not provide a `python` shim.
When reading environment variables in Python, use `os.environ[...]` (NOT `sys.environ[...]`).
Note: `yt-dlp` typically writes language-suffixed files like `subs.en.srt` (NOT `subs.srt`). Do not hardcode `transcript.srt` paths.
Instead, locate the downloaded `.srt` file (e.g. via `glob`) and convert that.

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

4. Minimal metadata (avoid huge tool outputs)
**Do NOT call `mcp__youtube__get_metadata`**. It often includes a huge formats list and can exceed tool/UI limits.
Instead, use `yt-dlp` with `--print` to capture only the fields we need (small output). Save this output into the artifact run dir.

```bash
yt-dlp --skip-download \
  --print "%(title)s" \
  --print "%(id)s" \
  --print "%(duration)s" \
  --print "%(channel)s" \
  --print "%(view_count)s" \
  "<URL>"
```

5. Visual analysis (best effort)
If `zai_vision` MCP tools are available, attempt to analyze video/segments and extract:
- key frames
- OCR of code/terminal output
- technical diagram interpretations

Notes:
- Tools are typically named like `mcp__zai_vision__...`.
- The service may have an 8MB limit; if needed, download low-res or split into segments in scratch.

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

Suggested implementation pattern (google.genai + url_context tool):

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-genai>=1.0.0", "requests>=2.31.0", "python-dotenv>=1.0.1"]
# ///

from __future__ import annotations

import argparse
import os
import sys
from google import genai
from google.genai import types
from dotenv import find_dotenv, load_dotenv

# Load env vars from the nearest .env (repo root, cwd, etc.). This avoids requiring
# the user to manually `export ...` before running the script.
load_dotenv(find_dotenv(usecwd=True))


def _api_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def build_client() -> genai.Client:
    key = _api_key()
    if not key:
        raise RuntimeError("Missing GOOGLE_API_KEY (or GEMINI_API_KEY)")
    return genai.Client(api_key=key)


def self_test() -> int:
    # No-secrets self-test: imports + tool construction only.
    _ = types.GenerateContentConfig(
        tools=[types.Tool(url_context=types.UrlContext())]
    )
    print("SELF_TEST_OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--url", action="append", dest="urls", default=[])
    ap.add_argument("--query", default="Summarize the key points.")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if not args.urls:
        print("Provide at least one --url", file=sys.stderr)
        return 2

    client = build_client()
    cfg = types.GenerateContentConfig(tools=[types.Tool(url_context=types.UrlContext())])
    prompt = f"Using these URLs: {args.urls}\\n\\n{args.query}"
    resp = client.models.generate_content(model="gemini-2.0-flash", contents=prompt, config=cfg)
    print(resp.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

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
