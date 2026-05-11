"""scripts/dev/csi_throttle_probe.py — characterize Z.AI proxy throttling.

Three test modes (one per invocation), each isolates one variable:

  --mode baseline   : 100 calls back-to-back, no pacing. Question: is the
                      proxy actually throttling at our usage level, or was
                      the Phase G slowdown from URL fetches / packet
                      variance? If latency stays smooth, throttling isn't
                      a problem. If it climbs / stalls, continue with
                      saturation + step-up.

  --mode saturation : 200 calls as fast as possible. Records call index
                      at which first >10s stall happens. That count =
                      burst capacity of the proxy's token bucket.

  --mode step-up    : 5/min for 1 min, then 10/min, 15/min, 20/min, 25/min,
                      30/min. Records the rate at which latency degrades.
                      That rate = sustained-throttle threshold.

Output: per-call CSV to ``/tmp/csi_throttle_<mode>_<timestamp>.csv``
        with columns: timestamp_iso, call_index, target_rate_per_min,
        latency_ms, status, error_msg.

The actual call payload mimics csi_intelligence_pass.analyze_action's
shape: ~2KB system prompt + ~5KB user message + 4096 max_tokens with a
structured-output tool. Same endpoint, same model (resolve_opus →
glm-5.1), same SDK as production.

Usage:
    PYTHONPATH=src uv run python scripts/dev/csi_throttle_probe.py --mode baseline
    PYTHONPATH=src uv run python scripts/dev/csi_throttle_probe.py --mode saturation
    PYTHONPATH=src uv run python scripts/dev/csi_throttle_probe.py --mode step-up

Env: needs ZAI_API_KEY / ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL set
(inherit from gateway: ``source /tmp/probe_env.sh`` works).
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

# A representative system prompt — shape matches csi_intelligence_pass
# without dragging in the entire 5KB prompt. Token count is similar.
SYSTEM_PROMPT = """\
You are a domain expert analyzing a single short text (a tweet) and producing
structured intelligence about the entities, concepts, products, features,
people, and events it references.

Domain glossary you should know:
- Products: Claude Code, Claude API, Claude Managed Agents, Claude Agent SDK,
  MCP (Model Context Protocol), Anthropic Console
- Models: Opus 4.7 / Opus 4.6, Sonnet 4.6 / Sonnet 4.5, Haiku 4.5
- Concepts: prompt caching, tool use, structured output, multiagent
  orchestration, outcomes loop

Taxonomy: each VaultAction has kind in {product, feature, concept, person, event}.

Rules:
- Multi-word names preserved as multi-word
- Do NOT extract t.co URL slugs, English stopwords, or joke words
- Empty vault_actions is valid for noise-only posts

Call the emit_vault_delta tool with structured JSON output. Don't return prose.
"""

# A representative test text — neutral, doesn't tickle special prompt rules.
TEST_USER_MSG = """\
# The post to analyze

- Handle: @ClaudeDevs
- Post ID: 9999999999999999999
- Tier: 2

## Post text

This is a synthetic test message for measuring LLM call latency under
sustained load. The content is intentionally neutral and shouldn't trigger
any specific extraction rules. The model should return an empty
vault_actions list since there's nothing here that's a real entity.

## Existing vault entities (1 total)

`existing-test-entity`

## Now emit the VaultDelta

Call emit_vault_delta with vault_actions: [].
"""

VAULT_DELTA_TOOL = {
    "name": "emit_vault_delta",
    "description": "Emit the structured analysis output.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vault_actions": {
                "type": "array",
                "items": {"type": "object"},
            },
            "post_summary": {"type": "string"},
        },
        "required": ["vault_actions"],
    },
}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="microseconds")


def _make_one_call(client, model: str) -> tuple[float, str, str]:
    """Run one LLM call, return (latency_ms, status, error_msg)."""
    start = time.perf_counter()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": TEST_USER_MSG}],
            tools=[VAULT_DELTA_TOOL],
            tool_choice={"type": "tool", "name": VAULT_DELTA_TOOL["name"]},
        )
        latency_ms = (time.perf_counter() - start) * 1000
        # Verify we got a tool_use block (otherwise the call shape was wrong)
        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                return latency_ms, "ok", ""
        return latency_ms, "no_tool_use", ""
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - start) * 1000
        return latency_ms, "error", f"{type(exc).__name__}: {str(exc)[:200]}"


def _setup_client():
    """Build an Anthropic SDK client targeting the Z.AI proxy."""
    from anthropic import Anthropic

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "No API key (checked ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ZAI_API_KEY)"
        )
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    kwargs = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return Anthropic(**kwargs)


def _resolve_opus_model() -> str:
    """Mirror utils.model_resolution.resolve_opus() — glm-5.1 via ZAI."""
    env_val = (os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL") or "").strip()
    if env_val:
        return env_val
    return "glm-5.1"


def _open_csv(mode: str) -> tuple[Path, csv.writer]:  # type: ignore[type-arg]
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(f"/tmp/csi_throttle_{mode.replace('-', '_')}_{ts}.csv")
    fh = path.open("w", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    writer.writerow(
        [
            "call_index",
            "timestamp_iso",
            "wall_seconds_from_start",
            "target_rate_per_min",
            "latency_ms",
            "status",
            "error_msg",
        ]
    )
    return path, writer, fh  # type: ignore[return-value]


def _print_progress_summary(results: list[dict]) -> None:
    """One-line stats so the operator can see throttling as it happens."""
    if not results:
        return
    last = results[-1]
    n = len(results)
    avg = sum(r["latency_ms"] for r in results) / n
    last_lat = last["latency_ms"]
    stalls = sum(1 for r in results if r["latency_ms"] > 10000)
    print(
        f"  call#{n:3d}  latency={last_lat:7.0f}ms  "
        f"running_avg={avg:5.0f}ms  stalls(>10s)={stalls}",
        flush=True,
    )


def run_baseline(client, model: str, n_calls: int = 100) -> list[dict]:
    """Mode A — back-to-back calls, no pacing.

    Question: is the proxy actually throttling, or was Phase G slowdown
    from URL fetches / packet variance?
    """
    print(f"\n=== BASELINE — {n_calls} calls back-to-back, no pacing ===")
    results = []
    start_wall = time.perf_counter()
    for i in range(1, n_calls + 1):
        latency_ms, status, err = _make_one_call(client, model)
        wall = time.perf_counter() - start_wall
        results.append(
            {
                "call_index": i,
                "timestamp_iso": _now_iso(),
                "wall_seconds_from_start": wall,
                "target_rate_per_min": 0,  # 0 = no rate cap
                "latency_ms": latency_ms,
                "status": status,
                "error_msg": err,
            }
        )
        _print_progress_summary(results)
    return results


def run_saturation(client, model: str, n_calls: int = 200) -> list[dict]:
    """Mode D — burst as fast as possible until first stall.

    Question: how many fast calls before the throttle kicks in? = burst
    capacity of the proxy's token bucket.
    """
    print(f"\n=== SATURATION — {n_calls} calls or first big stall, no pacing ===")
    results = []
    start_wall = time.perf_counter()
    first_stall_at = None
    for i in range(1, n_calls + 1):
        latency_ms, status, err = _make_one_call(client, model)
        wall = time.perf_counter() - start_wall
        results.append(
            {
                "call_index": i,
                "timestamp_iso": _now_iso(),
                "wall_seconds_from_start": wall,
                "target_rate_per_min": 0,
                "latency_ms": latency_ms,
                "status": status,
                "error_msg": err,
            }
        )
        _print_progress_summary(results)
        if latency_ms > 10000 and first_stall_at is None:
            first_stall_at = i
            print(f"\n  >>> FIRST >10s STALL at call #{i} (latency {latency_ms:.0f}ms)")
            print("  Continuing for 20 more calls to characterize stall pattern...")
            stall_target = i + 20
        elif first_stall_at is not None and i >= first_stall_at + 20:
            print(f"\n  Stopping after 20 post-stall calls. Burst capacity ≈ {first_stall_at}")
            break
    return results


def run_step_up(client, model: str) -> list[dict]:
    """Mode B — step-up sustained rate test.

    Question: what sustained rate triggers throttling? = refill rate of
    the proxy's token bucket.

    Plan: 5/min for 1 min, then 10/min for 1 min, then 15/min, 20/min,
    25/min, 30/min. Throttling appears when latency starts climbing or
    stalls become frequent.
    """
    print("\n=== STEP-UP — 5→10→15→20→25→30 calls/min, 1 min each ===")
    rates = [5, 10, 15, 20, 25, 30]
    results = []
    start_wall = time.perf_counter()
    call_index = 0
    for rate in rates:
        sleep_per_call = 60.0 / rate
        n = rate  # do exactly `rate` calls in `60s` at this pace
        print(f"\n--- step at {rate}/min ({n} calls, target sleep {sleep_per_call:.2f}s) ---")
        for j in range(n):
            call_index += 1
            t_call_start = time.perf_counter()
            latency_ms, status, err = _make_one_call(client, model)
            wall = time.perf_counter() - start_wall
            results.append(
                {
                    "call_index": call_index,
                    "timestamp_iso": _now_iso(),
                    "wall_seconds_from_start": wall,
                    "target_rate_per_min": rate,
                    "latency_ms": latency_ms,
                    "status": status,
                    "error_msg": err,
                }
            )
            _print_progress_summary(results)
            # Pace to target: total per-call slot is sleep_per_call seconds.
            # If latency exceeds the slot, no sleep (we're already late).
            elapsed_in_slot = time.perf_counter() - t_call_start
            remaining = sleep_per_call - elapsed_in_slot
            if remaining > 0:
                time.sleep(remaining)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Characterize Z.AI proxy throttling for CSI workloads"
    )
    parser.add_argument(
        "--mode",
        choices=["baseline", "saturation", "step-up"],
        required=True,
    )
    parser.add_argument(
        "--n-calls",
        type=int,
        default=None,
        help="Override default call count for baseline (100) / saturation (200)",
    )
    args = parser.parse_args()

    client = _setup_client()
    model = _resolve_opus_model()
    print(f"Model: {model}")
    print(f"Endpoint: {os.getenv('ANTHROPIC_BASE_URL') or '(default)'}")

    if args.mode == "baseline":
        results = run_baseline(client, model, args.n_calls or 100)
    elif args.mode == "saturation":
        results = run_saturation(client, model, args.n_calls or 200)
    else:
        results = run_step_up(client, model)

    csv_path, writer, fh = _open_csv(args.mode)
    for r in results:
        writer.writerow(
            [
                r["call_index"],
                r["timestamp_iso"],
                f"{r['wall_seconds_from_start']:.2f}",
                r["target_rate_per_min"],
                f"{r['latency_ms']:.0f}",
                r["status"],
                r["error_msg"],
            ]
        )
    fh.close()
    print(f"\n=== CSV saved: {csv_path} ===")

    # Final summary
    n = len(results)
    if n:
        latencies = [r["latency_ms"] for r in results]
        avg = sum(latencies) / n
        p50 = sorted(latencies)[n // 2]
        p95 = sorted(latencies)[max(0, int(n * 0.95) - 1)]
        max_lat = max(latencies)
        stalls = sum(1 for lat in latencies if lat > 10000)
        errors = sum(1 for r in results if r["status"] == "error")
        print()
        print(f"Total calls : {n}")
        print(f"Errors      : {errors}")
        print(f"Latency avg : {avg:7.0f} ms")
        print(f"Latency p50 : {p50:7.0f} ms")
        print(f"Latency p95 : {p95:7.0f} ms")
        print(f"Latency max : {max_lat:7.0f} ms")
        print(f"Stalls (>10s): {stalls}")
        print(f"Wall time   : {results[-1]['wall_seconds_from_start']:.1f} s")

    return 0


if __name__ == "__main__":
    sys.exit(main())
