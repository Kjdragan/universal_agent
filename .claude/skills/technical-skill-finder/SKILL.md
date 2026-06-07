---
name: technical-skill-finder
description: >
  Mine Claude Code session transcripts to discover which new skills should exist, surfacing
  recurring manual workflows, repeated errors, and repeated multi-step command sequences as
  ranked skill candidates. Use this skill whenever you hear: "find missing skills", "what
  skills should we build", "what skill should I create", "mine my sessions for skill ideas",
  "discover skills from usage", "find recurring pain points", "skill candidates", "where am I
  wasting time", "what keeps breaking", "turn my logs into skills", "skill gap analysis", or
  any time someone wants to derive new-skill ideas from real Claude Code (or optionally Codex)
  usage history. Discovers and ranks candidates, dedupes against the existing skill library,
  then hands accepted candidates to skill-creator (draft) and skill-judge (score). NOT for
  auditing the health of the existing library — use skill-audit-tf for that.
user-invocable: true
risk: safe
source: "Adapted from vincentkoc/dotskills (MIT) — technical-skill-finder."
---

# Technical Skill Finder

Find recurring pain points in real Claude Code usage and convert them into ranked, evidence-backed
new-skill candidates. This skill **discovers what to build**; it then hands each accepted candidate
downstream to `skill-creator` (draft) and `skill-judge` (score).

## When to use

- You want to discover missing skills from historical Claude Code activity, not from imagination.
- You want reproducible criteria and evidence before creating a new skill.
- You want to validate whether an existing skill already covers a pattern (dedupe).
- You want a ranked candidate report to feed into the skill-creation pipeline.

## When NOT to use

- **Auditing the existing library's health** (counts, dupes, missing/oversized SKILL.md, symlinks) →
  use `skill-audit-tf`. That job is library hygiene, not candidate discovery.
- **Drafting a skill you've already decided to build** → use `skill-creator`.
- **Scoring a drafted SKILL.md against spec** → use `skill-judge`.

## Where this sits in the skill lifecycle

```
[technical-skill-finder]  mine CC transcripts (Codex optional, consent-gated)
   → classify failures + cluster recurring workflows/errors/command sequences
   → dedupe against the existing skill library (.claude/skills/, ~82 with SKILL.md)
   → emit RANKED new-skill candidates (problem + evidence + frequency + scorecard)
        → hand each accepted candidate to  skill-creator  (draft)
              → skill-judge  (score the draft)
   (skill-audit-tf stays orthogonal: it audits library health, it is not a finder consumer)
```

## Inputs

- `SCOPE` (optional): repos / project dirs / tool domains to focus on. Default: all UA project transcripts.
- `TIMEFRAME` (optional): default `all`; otherwise filter on the envelope `timestamp` field.
- `TOP_N` (optional): number of highest-priority candidates to return. Default: 5.
- `CONSENT_CODEX` (optional): explicit approval to also read `~/.codex` sources. Default: off.
- `PRIVACY_POLICY` (required before reading any personal-signal source): see Guardrails.

## Primary source (Claude Code transcripts)

Glob: `/home/kjdragan/.claude/projects/-home-kjdragan-lrepos-universal-agent*/**.jsonl`
One project dir per cwd (main checkout + one per `.claude/worktrees/*`). Files are `0600`, one JSON
object per line. See `references/sources.md` for the precedence order, per-line schema, and the
Codex secondary source. The tested extraction recipes below assume the Claude Code record shape.

## Workflow

### 1. Initialize the source set
Enumerate transcripts and pick scope:
```bash
ls -t /home/kjdragan/.claude/projects/-home-kjdragan-lrepos-universal-agent*/*.jsonl | head
```
Default to Claude Code transcripts. Only touch `~/.codex` if `CONSENT_CODEX` is set (see Guardrails
and `references/sources.md`). Skip binary/corrupt lines; mine only parseable JSON.

### 2. Mine signals (commands, errors, prompts) — use the tested recipes

Set a glob and loop it over the corpus. All recipes below were validated against a real 1793-line
transcript (Recipe A → 53 Bash commands, Recipe B → 7 errors). Run per-file, then aggregate.

```bash
GLOB='/home/kjdragan/.claude/projects/-home-kjdragan-lrepos-universal-agent*/*.jsonl'
```

**Recipe A — every Bash command run (with ts / branch / cwd):**
```bash
for f in $GLOB; do jq -rc 'select(.type=="assistant") | . as $a
  | .message.content[]? | select(.type=="tool_use" and .name=="Bash")
  | {ts:$a.timestamp, branch:$a.gitBranch, cwd:$a.cwd, id:.id,
     cmd:.input.command, desc:.input.description}' "$f"; done
```
Swap `name=="Bash"` for `Edit`/`Read`/`Write`/`Agent`, or drop the name filter for all tool calls.

**Recipe B — error tool_results, ANSI-stripped, 200-char cap:**
```bash
for f in $GLOB; do jq -rc 'select(.type=="user") | . as $u
  | .message.content[]? | select(.type=="tool_result" and .is_error==true)
  | {ts:$u.timestamp, tool_use_id:.tool_use_id,
     text:((.content | if type=="array" then (map(.text? // (.content? // "")) | join("\n")) else (.|tostring) end)
           | gsub("\\[[0-9;]*m";"") | .[0:200])}' "$f"; done
```
Note: the failure flag is `is_error` (snake_case) and is **present only on failures** — there is no
`isError` variant. Strip ANSI (`[…m`) and any `<tool_use_error>…</tool_use_error>` wrapper.

**Recipe C — join each error back to the command that caused it (single slurp pass):**
```bash
for f in $GLOB; do jq -rs '([ .[] | select(.type=="assistant") | .message.content[]?
   | select(.type=="tool_use") | {key:.id, value:{name:.name, cmd:(.input.command // .input.file_path // "")}} ] | from_entries) as $tu
  | .[] | select(.type=="user") | .message.content[]?
  | select(.type=="tool_result" and .is_error==true)
  | {tool:($tu[.tool_use_id].name // "?"), cmd:($tu[.tool_use_id].cmd // "?"),
     err:((.content|tostring|gsub("\\[[0-9;]*m";"")))}' "$f"; done
```

**Recipe D — real human prompts (candidate-mining seed; excludes synthetic wrappers):**
```bash
for f in $GLOB; do jq -rc 'select(.type=="user" and (.message.content|type=="string")
   and (.message.content|startswith("<task-notification>")|not))
  | {ts:.timestamp, prompt:.message.content}' "$f"; done
```
Real human asks are `user` lines whose `.message.content` is a plain string; synthetic ones are
wrapped (`<task-notification>…`, `<task-id>…`) — the `startswith` guard drops them.

### 3. Classify failures
Tag each mined error/command into a failure class:
`auth` · `type-check` · `tool-error` · `git/ci` · `runtime` · `refactor-merge` · `test`.
Record **frequency**, **recency**, and **affected project context** (from `cwd`/`gitBranch`).

### 4. Cluster signals
Group by three axes: **domain** (python/js/rust/docs/tooling), **command lineage** (repeated
multi-step Bash sequences), and **error signature** (same cleaned error text recurring across
distinct sessions). Deprioritize low-recurrence one-offs. Recurring signatures (e.g. the same
"File has not been read yet" or a repeated worktree-guard error across sessions) are the strongest
new-skill signal.

### 5. Dedupe against the existing skill library
A candidate is "new" only if it doesn't overlap an existing skill's name or description intent.
```bash
ls -d /home/kjdragan/lrepos/universal_agent-wt-dotskills-finder/.claude/skills/*/   # ~86 dirs
for d in /home/kjdragan/lrepos/universal_agent-wt-dotskills-finder/.claude/skills/*/; do
  [ -f "${d}SKILL.md" ] && awk '/^---$/{n++; next} n==1' "${d}SKILL.md"; done
```
Caveats: a few dirs ship **no SKILL.md** (re-check each run — currently `freelance-scout`,
`nano-triple`, `notebooklm-orchestration-workspace`, `stitch-skills`). Many `description:` values are
YAML block scalars (`>` / `|`) — parse the folded/literal body, not just the inline line. High
overlap → propose a **skill update**; no overlap → propose a **new skill**.

### 6. Score and rank
Apply `references/scorecard.md`: score each candidate 0–5 on `frequency`, `impact`, `actionability`,
`toolability`, `novelty`; `confidence = average / 5`. Prioritize candidates where
`frequency >= 3` AND `confidence >= 0.72` AND `impact >= 3`. Return the top `TOP_N`.

### 7. Emit first-iteration candidate artifacts
For each high-priority candidate produce:
- Candidate title + scope, and `path` it would map to (`new` vs `update <existing-skill>`)
- Trigger-phrase examples (for the future description)
- Required inputs
- Suggested workflow summary
- Evidence snippets with anchors (`file.jsonl:line` + cleaned error/command text)
- Scorecard line (`frequency`, `impact`, `confidence`, `skill-fit: new|update`)
- Suggested deps/tools (`jq`, `rg`, shell utilities, MCP resources)

### 8. (Optional) Codex secondary pass — consent-gated
Only if `CONSENT_CODEX` is set. Codex transcripts use a different schema (branch on `.payload.type`,
not top-level `type`). Keep Codex-derived candidates clearly labeled. See `references/sources.md`.

## Output

A ranked candidate report **in chat** (not a written file), split into:
- `new` skill candidates (not yet covered) — ranked by scorecard
- `update` candidates (an existing skill should be extended) — name the target skill
- top source anchors / evidence references per candidate
- recommended next action: hand each accepted `new`/`update` candidate to `skill-creator`, then
  `skill-judge` to score the resulting draft.

## Guardrails (privacy / consent)

- **Never emit private transcript content** (prompts, secrets, paths, message bodies) into the report
  beyond the minimal evidence snippet needed, and redact credentials/private URLs.
- Reading `~/.codex` or any personal-signal source requires explicit consent (`CONSENT_CODEX` /
  `PRIVACY_POLICY`). Keep any personal-signal output **isolated** from coding-signal candidates unless
  the user explicitly asks for a merged scope.
- Never propose a skill with unresolved operational context (credentials, environment, private URLs).
- If evidence is ambiguous, return `confidence: low` and request one more session sample rather than
  inventing a candidate.

## References

- `references/sources.md` — transcript precedence, per-line schema, optional Codex source.
- `references/scorecard.md` — scoring dimensions and prioritization thresholds.
