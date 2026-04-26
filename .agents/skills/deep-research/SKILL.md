---
name: deep-research
description: Conduct autonomous, deep research tasks using the Gemini Deep Research Agent (preview). Use ONLY when the user explicitly requests "deep research" or "google deep research" to perform extensive literature review, analyze a topic deeply, or generate a comprehensive research report requiring multi-step web search, reading, and synthesis. Do NOT trigger for standard web searches or generic questions unless "deep research" is mentioned.
---

# Deep Research Skill

Use this skill when the user explicitly asks for "deep research" or "google deep research" on a specific topic. The Deep Research agent conducts multi-step research, planning, searching, reading, and reasoning over a period of minutes to generate highly comprehensive reports.

## Running the Research

This skill includes a Python script that orchestrates the Interactions API polling and streaming, handling reconnections and saving all output—including the final report, intermediate reasoning ("thoughts"), and generated visuals—to the workspace.

You MUST use the bundled script `scripts/run_research.py`. It is a standalone PEP 723 script intended to be run with `uv run`.

### Usage

```bash
uv run /home/kjdragan/lrepos/universal_agent/.agents/skills/deep-research/scripts/run_research.py \
  --prompt "Your research prompt here" \
  --output-dir "path/to/output/directory" \
  [--max]
```

### Arguments

- `--prompt`: The research prompt. Be as specific as possible. You can instruct the agent to use specific formatting, focus on certain aspects, or avoid guessing unknown data.
- `--output-dir`: The directory where the research outputs will be saved. You MUST provide a directory (e.g., the current session workspace). The script will create a subfolder inside it with a timestamped name containing the research log, final report, and any images.
- `--max`: (Optional) Use the `deep-research-max-preview-04-2026` agent instead of the standard `deep-research-preview-04-2026` agent. Use this flag if the user asks for maximum comprehensiveness, deep competitive landscape analysis, or extensive due diligence. Otherwise, omit it to use the standard agent.

## Output Format

The script will save all outputs in the specified output directory:
- `research_log.txt`: Contains the real-time stream of thought summaries and progress updates.
- `report.md`: The final markdown research report.
- `*.png` / `*.jpg`: Any visual elements generated during the research.

## Important Notes

- **Timeouts**: Deep research takes several minutes (sometimes 10-20 minutes). The script runs synchronously but handles the background polling internally. Do not interrupt it.
- **Cost**: Deep research consumes a significant amount of tokens and search queries. Only trigger this skill when explicitly requested.
- **Multimodal**: If the user provides a document or image to analyze, you cannot currently pass it to the script. The script currently only supports text prompts.
