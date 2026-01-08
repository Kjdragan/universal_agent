# Compaction Stress Probe

## Purpose
This probe is a standalone, repeatable harness to stress a sub-agent with a large
context payload and corpus, then force a report write. It is designed to surface
malformed tool calls (e.g., XML-like contamination in tool names) and compare
mitigation strategies like two-stage compaction.

The goal is to answer:
- Do malformed tool calls appear under heavy context load?
- Does a two-stage pipeline reduce tool-call corruption?
- Does host-side writing avoid tool-call formation failures?

## Script location
`scripts/compaction_stress_probe.py`

## What it does
1. Generates a large synthetic payload (in-memory) and a corpus file on disk.
2. Spawns a sub-agent tasked with reading the corpus and writing a report.
3. Logs tool use, malformed tool names, and tool errors.
4. Writes a `probe_results.json` summary into the run workspace.

## Key modes
- **baseline**: Single pass, sub-agent reads corpus and writes report.
- **two_stage**: First pass writes an outline, second pass reads the outline and
  writes the final report (fresh session).
- **write-mode tool**: Uses the Write tool (exercises tool-call formation).
- **write-mode host**: Returns report in text; the host writes it to disk.
- **tool-target composio**: Instructs the agent to call a Composio tool before writing.
  This is useful to detect malformed tool names that match the errors seen in harness logs.
- **inject-xml**: Adds XML-like snippets into the payload/corpus to test contamination.

## Usage examples
Baseline with moderate payload/corpus:
```bash
uv run python scripts/compaction_stress_probe.py \
  --strategy baseline \
  --payload-kb 128 \
  --corpus-kb 512
```

Two-stage compaction:
```bash
uv run python scripts/compaction_stress_probe.py \
  --strategy two_stage \
  --payload-kb 128 \
  --corpus-kb 512
```

Inject XML-like noise:
```bash
uv run python scripts/compaction_stress_probe.py --inject-xml
```

Force a Composio tool call (to test tool-name formation):
```bash
uv run python scripts/compaction_stress_probe.py --tool-target composio --inject-xml
```

Host-side write (bypass Write tool):
```bash
uv run python scripts/compaction_stress_probe.py --write-mode host
```

## Outputs
Each run creates a workspace directory like:
`AGENT_RUN_WORKSPACES/compaction_probe_YYYYMMDD_HHMMSS/`

Files:
- `corpus.txt`: generated corpus
- `report.md`: report output
- `outline.md`: only in two-stage mode
- `probe_results.json`: structured metrics

`probe_results.json` includes:
- `tool_uses`: list of tool names invoked
- `malformed_tool_uses`: tool names containing XML-like artifacts
- `tool_errors`: tool error summaries (tool_use_error, etc.)
- `assistant_text_len`: length of assistant text returned
- `assistant_text_preview`: short preview of assistant text (host mode)
- `result_error`: whether the SDK returned an error result

## Evaluation workflow
This probe is automated in the sense that it produces machine-readable results
for each run. It does not yet auto-rank strategies. You can compare runs by:
- Checking if `malformed_tool_uses` is empty
- Comparing `tool_errors` across strategies
- Comparing `result_error` and `assistant_text_len`

Recommended comparison matrix:
1) baseline + tool write
2) baseline + tool write + inject-xml
3) two_stage + tool write + inject-xml
4) two_stage + host write + inject-xml

If (3) reduces malformed tool calls relative to (2), two-stage compaction is
helping. If (4) succeeds when (3) fails, tool-call formation is the primary
failure and host-side write is a viable mitigation.

## Notes
- Composio mode requires the Composio MCP server to be configured. If not, the
  tool call will error, but the probe still captures whether the tool name was
  formed correctly.
- Use larger `--payload-kb` and `--corpus-kb` to increase stress until failures
  are observed.

## Next automation options (not implemented yet)
- Sweep payload/corpus sizes and emit a summary table.
- Auto-score runs and pick the best strategy.
- Plot failure rates vs. context size.
