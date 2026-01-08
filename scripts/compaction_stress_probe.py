#!/usr/bin/env python3
"""
Compaction stress probe for tool-call formation issues.

Usage:
  uv run python scripts/compaction_stress_probe.py \
    --strategy baseline \
    --stress-scope subagent \
    --payload-kb 64 \
    --corpus-kb 256
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Optional

from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, ClaudeSDKClient, HookMatcher
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock

XML_SNIPPET = (
    "<arg_key>tools</arg_key><arg_value>"
    "[{\"tool_slug\":\"COMPOSIO_SEARCH_NEWS\",\"arguments\":{\"query\":\"ai news\"}}]"
    "</arg_value>"
)


@dataclass
class ProbeStats:
    tool_uses: list[str]
    malformed_tool_uses: list[str]
    tool_errors: list[str]
    assistant_text_len: int
    assistant_text_preview: str
    result_error: bool
    session_id: Optional[str]


@dataclass
class ScenarioConfig:
    name: str
    strategy: str
    write_mode: str


@dataclass
class ScenarioOutcome:
    name: str
    attempt: int
    strategy: str
    write_mode: str
    workdir: str
    report_path: str
    report_bytes: int
    report_malformed_count: int
    report_tool_error_count: int
    report_result_error: bool
    total_malformed_count: int
    total_tool_error_count: int
    score: int
    success: bool
    duration_s: float
    details: dict[str, Any]


def _summarize_text(value: str, max_chars: int = 200) -> str:
    if not value:
        return ""
    compact = " ".join(value.split())
    if len(compact) > max_chars:
        return compact[: max_chars - 3] + "..."
    return compact


def _is_malformed_tool_name(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ("<", ">", "arg_key", "arg_value"))


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _build_payload(kb: int, inject_xml: bool) -> str:
    if kb <= 0:
        return ""
    base_line = (
        "Synthetic context line for stress testing. "
        "This is filler text to increase context size. "
    )
    target_bytes = kb * 1024
    parts: list[str] = []
    size = 0
    while size < target_bytes:
        chunk = (base_line * 6).strip() + "\n"
        parts.append(chunk)
        size += len(chunk)
    if inject_xml:
        parts.append("\n" + XML_SNIPPET + "\n")
    return "".join(parts)


def _write_corpus(path: str, kb: int, inject_xml: bool) -> None:
    payload = _build_payload(kb, inject_xml=inject_xml)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(payload)


def _build_composio_example() -> dict[str, Any]:
    return {
        "tools": [
            {
                "tool_slug": "COMPOSIO_SEARCH_NEWS",
                "arguments": {
                    "query": "artificial intelligence news",
                    "when": "w",
                    "gl": "us",
                    "hl": "en",
                },
            }
        ],
        "session_id": "probe",
        "current_step": "COMPOSIO_PROBE",
        "current_step_metric": "0/1",
        "sync_response_to_workbench": False,
        "thought": "Stress probe for tool-call formatting",
    }


def _build_report_prompt(
    corpus_path: str,
    output_path: str,
    payload: str,
    tool_target: str,
    write_mode: str,
    stage: str,
    min_words: int,
) -> str:
    header = (
        "Generate a report using the provided corpus. "
        "Read the corpus fully (in chunks if needed) before writing."
    )
    write_instruction = (
        f"Write the final report to {output_path} using the Write tool."
        if write_mode == "tool"
        else "Return the full report in plain text between REPORT_START and REPORT_END."
    )
    composio_step = ""
    if tool_target == "composio":
        example = json.dumps(_build_composio_example(), indent=2)
        composio_step = (
            "Before writing, call mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL "
            "with this JSON input (no XML or tags):\n"
            f"{example}\n"
        )
    stage_note = "" if stage == "report" else "Produce a concise outline instead of a full report."
    length_note = f"Target length: at least {min_words} words." if min_words else ""
    payload_block = "\n\nCONTEXT_PAYLOAD:\n" + payload if payload else ""
    return "\n\n".join(
        chunk
        for chunk in [
            header,
            f"Corpus path: {corpus_path}",
            length_note,
            write_instruction,
            composio_step,
            stage_note,
            payload_block,
        ]
        if chunk
    )


def _build_main_prompt(subagent_name: str, subagent_prompt: str) -> str:
    return (
        f"Use the {subagent_name} subagent to complete this task.\n"
        "Pass the instructions verbatim.\n\n"
        f"{subagent_prompt}"
    )


def _extract_report(text: str) -> str:
    if not text:
        return ""
    start = text.find("REPORT_START")
    if start == -1:
        return text.strip()
    end = text.find("REPORT_END", start)
    if end == -1:
        return text[start + len("REPORT_START") :].strip()
    return text[start + len("REPORT_START") : end].strip()


def _scenario_matrix(names: Optional[str]) -> list[ScenarioConfig]:
    scenarios = [
        ScenarioConfig("baseline_tool", "baseline", "tool"),
        ScenarioConfig("baseline_host", "baseline", "host"),
        ScenarioConfig("two_stage_tool", "two_stage", "tool"),
        ScenarioConfig("two_stage_host", "two_stage", "host"),
    ]
    if not names:
        return scenarios
    wanted = {name.strip() for name in names.split(",") if name.strip()}
    filtered = [scenario for scenario in scenarios if scenario.name in wanted]
    if not filtered:
        raise ValueError(f"No matching scenarios for --approaches={names}")
    return filtered


def _score_outcome(
    report_bytes: int,
    malformed_count: int,
    tool_error_count: int,
    result_error: bool,
    min_report_bytes: int,
) -> tuple[int, bool]:
    score = 0
    if report_bytes >= min_report_bytes:
        score += 4
    if malformed_count == 0:
        score += 2
    if tool_error_count == 0:
        score += 2
    if not result_error:
        score += 1
    success = report_bytes >= min_report_bytes and malformed_count == 0 and not result_error
    return score, success


def _aggregate_stats(stats_list: list[ProbeStats]) -> tuple[int, int, bool]:
    malformed = sum(len(stats.malformed_tool_uses) for stats in stats_list)
    tool_errors = sum(len(stats.tool_errors) for stats in stats_list)
    result_error = any(stats.result_error for stats in stats_list)
    return malformed, tool_errors, result_error


async def _run_query(
    client: ClaudeSDKClient,
    prompt: str,
    capture_text: bool = False,
) -> tuple[ProbeStats, Optional[str]]:
    await client.query(prompt)
    tool_uses: list[str] = []
    malformed: list[str] = []
    tool_errors: list[str] = []
    text_len = 0
    text_parts: list[str] = []
    result_error = False
    session_id: Optional[str] = None

    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    tool_uses.append(block.name)
                    if _is_malformed_tool_name(block.name):
                        malformed.append(block.name)
                elif isinstance(block, ToolResultBlock):
                    content = block.content
                    if isinstance(content, str):
                        result_text = content
                    else:
                        result_text = json.dumps(content) if content is not None else ""
                    if block.is_error or "tool_use_error" in result_text:
                        tool_errors.append(_summarize_text(result_text))
                elif isinstance(block, TextBlock):
                    text_len += len(block.text)
                    if capture_text:
                        text_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            result_error = message.is_error
            session_id = message.session_id

    combined_text = "".join(text_parts) if capture_text else None
    preview = _summarize_text(combined_text or "", max_chars=400)
    stats = ProbeStats(
        tool_uses=tool_uses,
        malformed_tool_uses=malformed,
        tool_errors=tool_errors,
        assistant_text_len=text_len,
        assistant_text_preview=preview,
        result_error=result_error,
        session_id=session_id,
    )
    return stats, combined_text


async def _run_scenario(
    scenario: ScenarioConfig,
    attempt: int,
    args: argparse.Namespace,
    base_dir: str,
    corpus_path: str,
    payload: str,
) -> ScenarioOutcome:
    scenario_dir = os.path.join(base_dir, f"{scenario.name}_r{attempt}")
    _ensure_dir(scenario_dir)

    output_path = os.path.join(scenario_dir, "report.md")
    outline_path = os.path.join(scenario_dir, "outline.md")

    subagent_name = "report-writer"
    subagent_tools = ["Read", "Write", "Grep", "Glob"]
    if args.tool_target == "composio":
        subagent_tools.append("mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL")

    allowed_tools = ["Task", "Read", "Write", "Grep", "Glob"]
    if args.tool_target == "composio":
        allowed_tools.append("mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL")

    options = ClaudeAgentOptions(
        model=args.model,
        permission_mode="acceptEdits",
        allowed_tools=allowed_tools,
        agents={
            subagent_name: AgentDefinition(
                description="Writes reports from large corpora under stress.",
                prompt="Follow the instructions precisely and write clear reports.",
                tools=subagent_tools,
            )
        },
    )

    if args.log_precompact:
        async def _log_precompact(input_data: dict, tool_use_id: Optional[str], context: Any) -> dict:
            keys = ",".join(sorted(input_data.keys()))
            print(f"[PreCompact] keys={keys}")
            return {}

        options.hooks = {"PreCompact": [HookMatcher(hooks=[_log_precompact])]}

    started = time.perf_counter()
    details: dict[str, Any] = {
        "strategy": scenario.strategy,
        "write_mode": scenario.write_mode,
        "tool_target": args.tool_target,
    }

    if scenario.strategy == "two_stage":
        outline_prompt = _build_report_prompt(
            corpus_path=corpus_path,
            output_path=outline_path,
            payload=payload,
            tool_target=args.tool_target,
            write_mode="tool",
            stage="outline",
            min_words=max(50, args.min_report_words // 2),
        )
        stage_one_prompt = _build_main_prompt(subagent_name, outline_prompt)
        async with ClaudeSDKClient(options=options) as client:
            stage_one_stats, _ = await _run_query(client, stage_one_prompt)
            details["stage_one"] = asdict(stage_one_stats)

        stage_two_prompt = _build_report_prompt(
            corpus_path=outline_path,
            output_path=output_path,
            payload="",
            tool_target=args.tool_target,
            write_mode=scenario.write_mode,
            stage="report",
            min_words=args.min_report_words,
        )
        stage_two_main = _build_main_prompt(subagent_name, stage_two_prompt)
        async with ClaudeSDKClient(options=options) as client:
            stage_two_stats, stage_two_text = await _run_query(
                client, stage_two_main, capture_text=scenario.write_mode == "host"
            )
            details["stage_two"] = asdict(stage_two_stats)
            if scenario.write_mode == "host" and stage_two_text:
                report_text = _extract_report(stage_two_text)
                with open(output_path, "w", encoding="utf-8") as handle:
                    handle.write(report_text)
        report_stats = stage_two_stats
        stage_stats = [stage_one_stats, stage_two_stats]
    else:
        main_prompt = _build_main_prompt(
            subagent_name,
            _build_report_prompt(
                corpus_path=corpus_path,
                output_path=output_path,
                payload=payload,
                tool_target=args.tool_target,
                write_mode=scenario.write_mode,
                stage="report",
                min_words=args.min_report_words,
            ),
        )
        async with ClaudeSDKClient(options=options) as client:
            stats, report_text = await _run_query(
                client, main_prompt, capture_text=scenario.write_mode == "host"
            )
            details["single_stage"] = asdict(stats)
            if scenario.write_mode == "host" and report_text:
                report_body = _extract_report(report_text)
                with open(output_path, "w", encoding="utf-8") as handle:
                    handle.write(report_body)
        report_stats = stats
        stage_stats = [stats]

    report_bytes = _file_size(output_path)
    report_malformed = len(report_stats.malformed_tool_uses)
    report_tool_errors = len(report_stats.tool_errors)
    report_result_error = report_stats.result_error

    total_malformed, total_tool_errors, _ = _aggregate_stats(stage_stats)
    score, success = _score_outcome(
        report_bytes=report_bytes,
        malformed_count=report_malformed,
        tool_error_count=report_tool_errors,
        result_error=report_result_error,
        min_report_bytes=args.min_report_bytes,
    )

    duration_s = time.perf_counter() - started
    return ScenarioOutcome(
        name=scenario.name,
        attempt=attempt,
        strategy=scenario.strategy,
        write_mode=scenario.write_mode,
        workdir=scenario_dir,
        report_path=output_path,
        report_bytes=report_bytes,
        report_malformed_count=report_malformed,
        report_tool_error_count=report_tool_errors,
        report_result_error=report_result_error,
        total_malformed_count=total_malformed,
        total_tool_error_count=total_tool_errors,
        score=score,
        success=success,
        duration_s=duration_s,
        details=details,
    )


def _summarize_outcomes(outcomes: list[ScenarioOutcome]) -> dict[str, Any]:
    summary: dict[str, Any] = {"by_strategy": {}, "best": {}}
    by_name: dict[str, list[ScenarioOutcome]] = {}
    for outcome in outcomes:
        by_name.setdefault(outcome.name, []).append(outcome)

    for name, runs in by_name.items():
        successes = sum(1 for run in runs if run.success)
        avg_score = sum(run.score for run in runs) / len(runs)
        avg_bytes = sum(run.report_bytes for run in runs) / len(runs)
        summary["by_strategy"][name] = {
            "runs": len(runs),
            "successes": successes,
            "success_rate": successes / len(runs),
            "avg_score": round(avg_score, 2),
            "avg_report_bytes": round(avg_bytes, 1),
            "best_score": max(run.score for run in runs),
            "min_report_bytes": min(run.report_bytes for run in runs),
            "max_report_bytes": max(run.report_bytes for run in runs),
        }

    def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[float, float]:
        data = item[1]
        return (data["success_rate"], data["avg_score"])

    if summary["by_strategy"]:
        best_name, best_data = max(summary["by_strategy"].items(), key=_sort_key)
        summary["best"] = {"name": best_name, **best_data}

    return summary


async def _run_probe(args: argparse.Namespace) -> int:
    run_id = time.strftime("%Y%m%d_%H%M%S")
    base_dir = args.workdir or os.path.join("AGENT_RUN_WORKSPACES", f"compaction_probe_{run_id}")
    _ensure_dir(base_dir)

    shared_dir = os.path.join(base_dir, "shared")
    _ensure_dir(shared_dir)

    corpus_path = os.path.join(shared_dir, "corpus.txt")
    _write_corpus(corpus_path, kb=args.corpus_kb, inject_xml=args.inject_xml)
    payload = _build_payload(args.payload_kb, inject_xml=args.inject_xml)

    if args.strategy == "auto":
        scenarios = _scenario_matrix(args.approaches)
    else:
        scenarios = [ScenarioConfig("single", args.strategy, args.write_mode)]

    outcomes: list[ScenarioOutcome] = []
    for attempt in range(1, args.repeat + 1):
        for scenario in scenarios:
            outcome = await _run_scenario(
                scenario=scenario,
                attempt=attempt,
                args=args,
                base_dir=base_dir,
                corpus_path=corpus_path,
                payload=payload,
            )
            outcomes.append(outcome)
            print(
                f"[{scenario.name} r{attempt}] score={outcome.score} "
                f"report_bytes={outcome.report_bytes} malformed={outcome.report_malformed_count} "
                f"errors={outcome.report_tool_error_count}"
            )

    summary = _summarize_outcomes(outcomes)
    report_path = os.path.join(base_dir, "probe_results.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "run_id": run_id,
                "payload_kb": args.payload_kb,
                "corpus_kb": args.corpus_kb,
                "tool_target": args.tool_target,
                "inject_xml": args.inject_xml,
                "min_report_bytes": args.min_report_bytes,
                "min_report_words": args.min_report_words,
                "outcomes": [asdict(outcome) for outcome in outcomes],
                "summary": summary,
            },
            handle,
            indent=2,
        )

    print("Probe complete:")
    print(f"- base_dir: {base_dir}")
    print(f"- report: {report_path}")
    if summary.get("best"):
        print(f"- best: {summary['best'].get('name')}")

    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compaction stress probe")
    parser.add_argument(
        "--strategy",
        choices=["baseline", "two_stage", "auto"],
        default="baseline",
        help="Probe strategy (default: baseline). Use 'auto' to run a matrix.",
    )
    parser.add_argument(
        "--approaches",
        default=None,
        help="Comma-separated scenario names for auto mode (e.g. baseline_tool,two_stage_host).",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat each scenario N times (default: 1).",
    )
    parser.add_argument(
        "--stress-scope",
        choices=["subagent"],
        default="subagent",
        help="Stress scope (currently only subagent).",
    )
    parser.add_argument(
        "--payload-kb",
        type=int,
        default=64,
        help="Synthetic payload size in KB (default: 64).",
    )
    parser.add_argument(
        "--corpus-kb",
        type=int,
        default=256,
        help="Corpus file size in KB (default: 256).",
    )
    parser.add_argument(
        "--min-report-bytes",
        type=int,
        default=1,
        help="Minimum report size in bytes to count as success (default: 1).",
    )
    parser.add_argument(
        "--min-report-words",
        type=int,
        default=200,
        help="Minimum report length in words (default: 200).",
    )
    parser.add_argument(
        "--inject-xml",
        action="store_true",
        help="Inject XML-like snippet into the payload and corpus.",
    )
    parser.add_argument(
        "--tool-target",
        choices=["write", "composio"],
        default="write",
        help="Tool to exercise during the probe (default: write).",
    )
    parser.add_argument(
        "--write-mode",
        choices=["tool", "host"],
        default="tool",
        help="Write via tool call or host capture (default: tool).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model override.",
    )
    parser.add_argument(
        "--workdir",
        default=None,
        help="Override output directory.",
    )
    parser.add_argument(
        "--log-precompact",
        action="store_true",
        help="Log PreCompact hook invocations.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run_probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
