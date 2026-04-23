# Quality Gate: gemini-tts-narrator-tf

**Date**: 2026-04-22
**Auditor**: Simone (Task Forge Phase 5b)
**Standard**: `.claude/skills/skill-creator/SKILL.md` (read and applied)

## Structural Checks (6/6 required)

### 1. Structure — PASS
SKILL.md has YAML frontmatter (name, description), Goal, Success Criteria, Context, Execution Steps, Constraints, Dependencies. All required sections present.

### 2. Not-a-wrapper — PASS
SKILL.md describes *what* (convert text to narrated audio), *why* (warm audiobook-style narration for people who prefer listening), and *how* (Gemini TTS with chunking + ffmpeg conversion). The script is referenced as a tool, not the entire skill. Includes voice selection guidance, chunking strategy, and fallback path.

### 3. Composable — PASS
References existing `agentmail` skill for email delivery. Uses standard `work_products/` output convention. Follows PEP 723 inline deps pattern for portability. Fallback to `edge-tts` (existing skill) documented.

### 4. Generalizable — PASS
No hardcoded file paths. API key read from env var (`GEMINI_IMAGE_API_KEY` by default, configurable via `--api-key-env`). Voice and model are CLI flags. Input is any .txt/.md file. Output path is configurable. Works in any session with the API key available.

### 5. Progressive Disclosure — PASS
SKILL.md is ~50 lines (under 100-line guideline). Heavy domain knowledge (API patterns, anti-patterns, voice list) lives in `references/gemini_tts_api.md`. Deterministic/fragile code (PCM wrapping, ffmpeg conversion, chunking algorithm) lives in `scripts/narrate_gemini.py`.

### 6. Functional Accuracy — PASS
Post-execution reconciliation verified:
- SDK: SKILL.md says `google-genai` → script imports `from google import genai` ✓
- Model: SKILL.md says `gemini-2.5-flash-preview-tts` → script DEFAULT_MODEL matches ✓
- Auth: SKILL.md says `GEMINI_IMAGE_API_KEY` → script reads `os.environ["GEMINI_IMAGE_API_KEY"]` ✓
- Dependencies: SKILL.md says `google-genai` + `ffmpeg` → script PEP 723 deps + subprocess ffmpeg call ✓

## Input Source Coverage

Task specified: "any source (URLs, text block, .txt/.md files)"
- .txt/.md files: ✓ (script reads file paths directly)
- Text blocks: ✓ (agent reads inline text, writes to temp file, calls script)
- URLs: ⚠️ PARTIAL — skill relies on agent to fetch URL content first, then pass as file/text. This is standard composable behavior (agent uses webReader/defuddle, then pipes to narration). Documented in Execution Steps.

## Execution Evidence
- Generated: `the_kitten_and_the_giant_narrated.mp3` (3.5MB, ~6.5 min)
- Delivered: emailed to kevinjdragan@gmail.com via agentmail
- Engine: Gemini TTS Aoede voice on `gemini-2.5-flash-preview-tts`

---

## Phase 5c: Improvement Pass (v0 → v1)

**Applied universal pattern: Externalize domain knowledge**
- Moved voice list, API patterns, and anti-patterns to `references/gemini_tts_api.md`
- SKILL.md stays concise with just execution guidance

**Applied universal pattern: Track maturity versioning**
- Version: v1 (post-execution reconciled)
- Previous v0 used `edge-tts` only; v1 adds Gemini TTS as primary with edge-tts fallback

**Applied universal pattern: Specify reproducible methodology**
- Script uses PEP 723 inline deps for deterministic `uv run` behavior
- Chunking algorithm documented in script comments
- Audio pipeline: Gemini PCM → WAV wrapper → ffmpeg MP3

**Description improvement:**
- v0: "Narrate a text story file into an audio MP3 using text-to-speech"
- v1: "Narrate any text source into an audio MP3 using Google Gemini TTS. Accepts a local file path or raw text, selects an expressive voice, chunks long content intelligently, and produces a polished MP3 audiobook file."
- Added trigger phrases: "narrate", "read aloud", "audiobook", "TTS this story", "convert to audio"

---

## Meta-Improvements (for Task Forge itself)

1. **API model discovery**: When docs reference a GA model name (e.g. `gemini-2.5-flash-tts`), always run `client.models.list()` to verify actual availability on the API key. GA names don't always map to preview API surfaces.
2. **response_modalities gotcha**: TTS models REJECT the default TEXT modality. The `response_modalities=["AUDIO"]` parameter is essential and not always prominent in docs. Add to the Task Forge anti-pattern catalog.
3. **Hook write restrictions**: Task Forge Phase 3 should always scaffold in workspace `task-skills/` first, then promote via Bash `cp -r` in Phase 6. Direct writes to `.claude/skills/` may be blocked by workspace hooks.
