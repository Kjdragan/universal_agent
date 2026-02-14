#!/usr/bin/env python3
"""
Benchmark Claude Agent SDK latency across model aliases (opus/sonnet/haiku).

This measures end-to-end wall latency for a single query-response cycle using
Claude Code's streaming JSON protocol via claude-agent-sdk.

Typical usage:
  uv run python scripts/bench_agent_sdk_model_latency.py
  uv run python scripts/bench_agent_sdk_model_latency.py --runs 5 --warmup 1
  uv run python scripts/bench_agent_sdk_model_latency.py --models opus,sonnet,haiku --prompt "2+2?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from claude_agent_sdk.client import ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
)


DEFAULT_PROMPT = "Compute 1234*5678. Respond with only the integer, no other text."
DEFAULT_MODELS = ["opus", "sonnet", "haiku"]
DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def _parse_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _load_settings_env(settings_path: Path | None) -> dict[str, str]:
    if not settings_path:
        return {}
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    env = payload.get("env")
    if isinstance(env, dict):
        return {str(k): str(v) for k, v in env.items() if isinstance(k, str) and v is not None}
    return {}


def _resolve_alias_to_model(alias: str, settings_env: dict[str, str]) -> str:
    key_by_alias = {
        "opus": "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    }
    key = key_by_alias.get(alias.lower())
    if not key:
        return alias
    return (settings_env.get(key) or "").strip() or alias

async def _run_one_query(*, client: ClaudeSDKClient, prompt: str) -> dict[str, Any]:
    start = time.perf_counter()
    await client.query(prompt)

    first_text_s: float | None = None
    text_parts: list[str] = []
    result_msg: ResultMessage | None = None

    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock) and block.text:
                    if first_text_s is None:
                        first_text_s = time.perf_counter() - start
                    text_parts.append(block.text)
        elif isinstance(msg, ResultMessage):
            result_msg = msg
            break

    end = time.perf_counter()
    return {
        "wall_s": end - start,
        "first_text_s": first_text_s,
        "text": "".join(text_parts).strip(),
        "result": asdict(result_msg) if result_msg is not None else None,
    }


def _summarize(samples: list[float]) -> dict[str, float]:
    if not samples:
        return {}
    sorted_samples = sorted(samples)
    n = len(sorted_samples)

    def pct(p: float) -> float:
        if n == 1:
            return sorted_samples[0]
        k = (n - 1) * p
        f = int(k)
        c = min(f + 1, n - 1)
        if f == c:
            return sorted_samples[f]
        d0 = sorted_samples[f] * (c - k)
        d1 = sorted_samples[c] * (k - f)
        return d0 + d1

    return {
        "n": float(n),
        "min": sorted_samples[0],
        "p50": pct(0.50),
        "p90": pct(0.90),
        "p95": pct(0.95),
        "max": sorted_samples[-1],
        "mean": statistics.fmean(sorted_samples),
    }


async def _bench_model(
    *,
    model: str,
    prompt: str,
    warmup: int,
    runs: int,
    settings_path: Path | None,
    print_responses: bool,
    debug_stderr: bool,
    query_timeout_s: float,
) -> dict[str, Any]:
    # Important: disable tools for this benchmark to isolate base inference latency.
    def _stderr(line: str) -> None:
        if not debug_stderr:
            return
        # Claude Code can be chatty in verbose mode; keep this terse.
        print(f"[claude:{model}] {line.rstrip()}", flush=True)

    options = ClaudeAgentOptions(
        model=model,
        permission_mode="bypassPermissions",
        system_prompt="You are a helpful assistant. Follow the user instructions exactly.",
        tools=[],
        mcp_servers={},
        settings=str(settings_path) if settings_path else None,
        # Keep env empty; if settings.json includes env, Claude Code will apply it.
        env={},
        # Prevent long reasoning bursts from polluting latency comparisons.
        max_thinking_tokens=0,
        stderr=_stderr,
    )

    # (1) Start the client once per model so startup cost is amortized.
    client = ClaudeSDKClient(options)
    connect_start = time.perf_counter()
    await client.connect(prompt=None)
    connect_s = time.perf_counter() - connect_start

    try:
        # (2) Warmup runs (excluded from stats)
        warmup_errors: list[str] = []
        for i in range(max(0, warmup)):
            try:
                await asyncio.wait_for(
                    _run_one_query(client=client, prompt=prompt),
                    timeout=max(5.0, query_timeout_s),
                )
            except Exception as exc:
                warmup_errors.append(f"warmup[{i+1}]: {type(exc).__name__}: {exc}")

        # (3) Measured runs
        wall_s: list[float] = []
        first_text_s: list[float] = []
        api_duration_s: list[float] = []
        api_duration_api_s: list[float] = []
        run_errors: list[str] = []

        for i in range(max(1, runs)):
            try:
                sample = await asyncio.wait_for(
                    _run_one_query(client=client, prompt=prompt),
                    timeout=max(5.0, query_timeout_s),
                )
            except Exception as exc:
                run_errors.append(f"run[{i+1}]: {type(exc).__name__}: {exc}")
                continue
            wall_s.append(float(sample["wall_s"]))

            ft = sample.get("first_text_s")
            if isinstance(ft, (float, int)):
                first_text_s.append(float(ft))

            result = sample.get("result") or {}
            if isinstance(result, dict):
                dur_ms = result.get("duration_ms")
                dur_api_ms = result.get("duration_api_ms")
                if isinstance(dur_ms, (float, int)):
                    api_duration_s.append(float(dur_ms) / 1000.0)
                if isinstance(dur_api_ms, (float, int)):
                    api_duration_api_s.append(float(dur_api_ms) / 1000.0)

            if print_responses:
                print(f"\n[{model}] run {i+1}/{runs} response:\n{sample.get('text','')}\n")
    finally:
        await client.disconnect()

    return {
        "model": model,
        "connect_s": connect_s,
        "warmup": warmup,
        "runs_requested": runs,
        "warmup_errors": warmup_errors,
        "run_errors": run_errors,
        "wall_s": _summarize(wall_s),
        "first_text_s": _summarize(first_text_s),
        "result_duration_s": _summarize(api_duration_s),
        "result_duration_api_s": _summarize(api_duration_api_s),
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark Claude Agent SDK latency across model aliases.")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt to run for each benchmark sample.")
    ap.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated model aliases to test (default: opus,sonnet,haiku).",
    )
    ap.add_argument("--runs", type=int, default=3, help="Measured runs per model (default: 3).")
    ap.add_argument("--warmup", type=int, default=1, help="Warmup runs per model (default: 1).")
    ap.add_argument(
        "--settings",
        default=str(DEFAULT_SETTINGS_PATH),
        help="Path to Claude Code settings.json (default: ~/.claude/settings.json). Use empty to disable.",
    )
    ap.add_argument(
        "--model-mode",
        choices=["alias", "resolved"],
        default="alias",
        help="Use raw aliases (opus/sonnet/haiku) or resolve aliases via settings.json env (default: alias).",
    )
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of pretty text.")
    ap.add_argument("--out", default="", help="Optional path to write JSON results to disk.")
    ap.add_argument("--print-responses", action="store_true", help="Print responses for debugging.")
    ap.add_argument("--timeout-s", type=float, default=60.0, help="Timeout per query in seconds (default: 60).")
    ap.add_argument("--debug-stderr", action="store_true", help="Print Claude Code stderr (verbose) during runs.")
    args = ap.parse_args()

    settings_path: Path | None = None
    if str(args.settings).strip():
        settings_path = Path(args.settings).expanduser().resolve()
        if not settings_path.exists():
            raise SystemExit(f"--settings path not found: {settings_path}")

    settings_env = _load_settings_env(settings_path)

    models = _parse_csv(args.models)
    if not models:
        raise SystemExit("--models must include at least one model alias")

    results: list[dict[str, Any]] = []
    for model in models:
        model_value = model
        if args.model_mode == "resolved":
            model_value = _resolve_alias_to_model(model, settings_env)
        print(
            f"[bench] model={model} -> {model_value} (mode={args.model_mode}) warmup={args.warmup} runs={args.runs}",
            flush=True,
        )
        try:
            results.append(
                await _bench_model(
                    model=model_value,
                    prompt=args.prompt,
                    warmup=args.warmup,
                    runs=args.runs,
                    settings_path=settings_path,
                    print_responses=args.print_responses,
                    debug_stderr=args.debug_stderr,
                    query_timeout_s=float(args.timeout_s),
                )
            )
        except Exception as exc:
            results.append(
                {
                    "model": model_value,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    payload = {
        "settings_path": str(settings_path) if settings_path else None,
        "model_mode": args.model_mode,
        "settings_env_models": {
            "ANTHROPIC_DEFAULT_OPUS_MODEL": settings_env.get("ANTHROPIC_DEFAULT_OPUS_MODEL"),
            "ANTHROPIC_DEFAULT_SONNET_MODEL": settings_env.get("ANTHROPIC_DEFAULT_SONNET_MODEL"),
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": settings_env.get("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
        },
        "prompt": args.prompt,
        "warmup": args.warmup,
        "runs": args.runs,
        "results": results,
    }

    if str(args.out).strip():
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("\nClaude Agent SDK latency benchmark\n")
    print(f"settings: {payload['settings_path'] or '(none)'}")
    print(f"prompt: {args.prompt}")
    print(f"warmup: {args.warmup} | runs: {args.runs}\n")

    for row in results:
        model = row["model"]
        print(f"== {model} ==")
        if "error" in row:
            print(f"error: {row['error']}")
            print("")
            continue
        print(f"connect_s: {row['connect_s']:.3f}")
        if row.get("warmup_errors"):
            print(f"warmup_errors: {len(row['warmup_errors'])}")
        if row.get("run_errors"):
            print(f"run_errors: {len(row['run_errors'])}")
        for k in ["wall_s", "first_text_s", "result_duration_s", "result_duration_api_s"]:
            stats = row.get(k) or {}
            if not stats:
                continue
            print(
                f"{k}: n={int(stats['n'])} p50={stats['p50']:.3f} p90={stats['p90']:.3f} "
                f"p95={stats['p95']:.3f} mean={stats['mean']:.3f} min={stats['min']:.3f} max={stats['max']:.3f}"
            )
        print("")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
