#!/usr/bin/env python3
"""GLM-5.2 opus-replacement evaluation harness — production-representative.

WHY THIS EXISTS
---------------
ZAI shipped GLM-5.2 (new flagship). We want to evaluate it as the opus-tier
replacement for ``glm-5.1`` WITHOUT touching any production setting (no env flip,
no ``ZAI_MODEL_MAP`` edit). A naive standalone probe (raw httpx from the desktop)
is the wrong test bed for two reasons proven on 2026-06-13:
  1. It bypasses UA's instrumentation, so it is INVISIBLE to the ZAI Control panel
     (the panel is 100% client-side; capture is installed by
     ``initialize_runtime_secrets`` → ``install_zai_observability``).
  2. It bypasses ``rate_limiter.with_rate_limit_retry``, so an unpaced burst trips
     ZAI's Fair-Usage 1313 throttle instantly (and from the wrong egress IP).

This harness fixes all of that by running through UA's REAL path:
  * ``initialize_runtime_secrets()`` first  → ZAI secrets injected + httpx capture
    hooks installed, so every call below lands in ``zai_inference_events.jsonl``
    and shows on the panel (caller = this module).
  * Every model call routed through ``with_rate_limit_retry(model_tier="opus")``
    → AIMD pacing + per-tier (cap-1 opus) concurrency, shared with production, so
    it is FUP-respectful and cannot burst.
  * Intended to run ON THE VPS (prod egress IP, prod event file, prod FUP bucket).

WHAT IT TESTS
-------------
GLM-5.1 had NO thinking mode; GLM-5.2 ADDS it and defaults it ON. Two lanes:
  - SDK lane (the exact path UA's services use, via ``anthropic.AsyncAnthropic``):
      default (no thinking) · thinking disabled · thinking enabled+budget.
    Also reproduces UA's content-extraction (``for b in resp.content: b.text``) to
    confirm UA's parser gets NON-EMPTY text under each shape.
  - Raw-httpx lane (for ZAI-specific shapes the SDK validates away client-side):
      thinking={"type":"enabled"} (no budget) · thinking={"type":"auto"}.
    Dumps the raw response block structure.

Both lanes are captured by the observability hook and paced by the limiter.

RUN (on the VPS, as user ua, from the deployed checkout)::

    cd /opt/universal_agent && ./.venv/bin/python -m universal_agent.scripts.glm52_probe
    # or: uv run python -m universal_agent.scripts.glm52_probe

Env knobs (all optional):
  UA_GLM52_MODEL       wire model id (default: glm-5.2)
  UA_GLM52_TIER        limiter tier bucket (default: opus)
  UA_GLM52_MAXTOK      max_tokens for non-thinking calls (default: 512)
  UA_GLM52_BUDGET      thinking budget_tokens (default: 1024; max_tokens=2*budget)
  UA_GLM52_LANES       comma list of lanes to run: sdk,raw (default: sdk,raw)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

PROMPT = "What is 17 * 23? Give the number, then one short sentence of reasoning."


def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v.strip() if v and v.strip() else default


MODEL = _env("UA_GLM52_MODEL", "glm-5.2")
TIER = _env("UA_GLM52_TIER", "opus")
MAXTOK = int(_env("UA_GLM52_MAXTOK", "512"))
BUDGET = int(_env("UA_GLM52_BUDGET", "1024"))
LANES = {x.strip() for x in _env("UA_GLM52_LANES", "sdk,raw").split(",") if x.strip()}


# ── response inspection helpers ──────────────────────────────────────────
def _inspect_sdk(resp: Any) -> dict:
    """Summarize an anthropic SDK Message, reproducing UA's text extraction."""
    ua_text = ""
    blocks = []
    think_chars = 0
    for b in getattr(resp, "content", []) or []:
        bt = getattr(b, "type", "?")
        if hasattr(b, "text"):
            ua_text += b.text
            blocks.append(f"{bt}(text:{len(b.text)})")
        elif hasattr(b, "thinking"):
            tk = b.thinking or ""
            think_chars += len(tk)
            blocks.append(f"{bt}(thinking:{len(tk)})")
        else:
            blocks.append(bt)
    usage = getattr(resp, "usage", None)
    usage_d = {}
    if usage is not None:
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
            v = getattr(usage, k, None)
            if v is not None:
                usage_d[k] = v
    return {
        "model": getattr(resp, "model", None),
        "stop_reason": getattr(resp, "stop_reason", None),
        "blocks": blocks,
        "ua_text_chars": len(ua_text.strip()),
        "thinking_chars": think_chars,
        "ua_text_preview": ua_text.strip()[:160],
        "usage": usage_d,
    }


def _inspect_raw(obj: dict) -> dict:
    if "error" in obj:
        return {"error": obj["error"]}
    text = think = 0
    blocks = []
    for b in obj.get("content") or []:
        bt = b.get("type")
        if bt == "text":
            text += len(b.get("text") or "")
            blocks.append(f"text:{len(b.get('text') or '')}")
        elif bt in ("thinking", "redacted_thinking"):
            t = b.get("thinking") or b.get("data") or ""
            think += len(str(t))
            blocks.append(f"{bt}:{len(str(t))}")
        else:
            blocks.append(f"{bt}{list(b.keys())}")
    for alt in ("reasoning_content", "reasoning"):
        if obj.get(alt):
            blocks.append(f"TOPLEVEL.{alt}:{len(str(obj[alt]))}")
    return {
        "model": obj.get("model"),
        "stop_reason": obj.get("stop_reason"),
        "blocks": blocks,
        "text_chars": text,
        "thinking_chars": think,
        "usage": {k: v for k, v in (obj.get("usage") or {}).items() if isinstance(v, (int, float))},
    }


def _print(label: str, summary: dict) -> None:
    print("\n" + "-" * 72)
    print(f"[{label}]")
    if "error" in summary:
        e = summary["error"]
        print(f"  ERROR type={e.get('type')} code={e.get('code')} :: {str(e.get('message'))[:160]}")
        return
    verdict_chars = summary.get("ua_text_chars", summary.get("text_chars", 0))
    verdict = "NON-EMPTY TEXT ✅" if verdict_chars > 0 else "!!! EMPTY TEXT !!!"
    print(f"  model={summary.get('model')} stop_reason={summary.get('stop_reason')} usage={summary.get('usage')}")
    print(f"  blocks={summary.get('blocks')}  thinking_chars={summary.get('thinking_chars')}")
    if "ua_text_preview" in summary:
        print(f"  UA-parser text ({summary['ua_text_chars']} chars): {summary['ua_text_preview']!r}")
    print(f"  => {verdict}")


# ── the two lanes ────────────────────────────────────────────────────────
async def _run() -> int:
    from universal_agent.rate_limiter import with_rate_limit_retry
    from universal_agent.utils.model_resolution import model_id_to_tier

    base_url = os.getenv("ANTHROPIC_BASE_URL")
    token = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
    if not base_url or not token:
        print("FATAL: ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN not in env after bootstrap.")
        return 1

    default_tier = model_id_to_tier(MODEL)
    print(f"\nGLM-5.2 harness :: model={MODEL} forced_tier={TIER} "
          f"(model_id_to_tier would bucket it as '{default_tier}')")
    print(f"  endpoint={base_url}/v1/messages  lanes={sorted(LANES)}")

    budget_max = max(MAXTOK, BUDGET * 2)

    # ---- SDK lane (production path) ----
    if "sdk" in LANES:
        import anthropic as _a
        from anthropic import AsyncAnthropic
        print(f"\n########## SDK LANE (anthropic {getattr(_a, '__version__', '?')}, via with_rate_limit_retry) ##########")
        # Client built AFTER initialize_runtime_secrets() so its internal httpx
        # client is monkey-patched for observability. max_retries=0 — the limiter
        # owns retry policy (mirrors services/llm_classifier.py::_get_anthropic_client).
        client = AsyncAnthropic(api_key=token, base_url=base_url, timeout=120.0, max_retries=0)
        sdk_cases = [
            ("sdk: default (no thinking) — UA's call today", {"max_tokens": MAXTOK}),
            ("sdk: thinking disabled", {"max_tokens": MAXTOK, "thinking": {"type": "disabled"}}),
            ("sdk: thinking enabled+budget", {"max_tokens": budget_max,
                                              "thinking": {"type": "enabled", "budget_tokens": BUDGET}}),
        ]
        try:
            for label, extra in sdk_cases:
                try:
                    resp = await with_rate_limit_retry(
                        client.messages.create,
                        context="glm52_probe.sdk",
                        model_tier=TIER,
                        max_total_seconds=300,
                        model=MODEL,
                        messages=[{"role": "user", "content": PROMPT}],
                        **extra,
                    )
                    _print(label, _inspect_sdk(resp))
                except Exception as exc:  # noqa: BLE001
                    print(f"\n[{label}]\n  EXCEPTION {type(exc).__name__}: {str(exc)[:240]}")
        finally:
            await client.close()

    # ---- Raw-httpx lane (ZAI-specific thinking shapes) ----
    if "raw" in LANES:
        import httpx
        print("\n########## RAW-HTTPX LANE (ZAI shapes, via with_rate_limit_retry) ##########")
        url = f"{base_url}/v1/messages"
        headers = {"x-api-key": token, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        raw_cases = [
            ("raw: thinking={type:enabled} (no budget)", {"thinking": {"type": "enabled"}, "max_tokens": budget_max}),
            ("raw: thinking={type:auto}", {"thinking": {"type": "auto"}, "max_tokens": MAXTOK}),
        ]
        # AsyncClient created post-bootstrap -> hooked for observability.
        async with httpx.AsyncClient(timeout=120.0) as hc:
            for label, extra in raw_cases:
                payload = {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}], **extra}

                async def _post():
                    r = await hc.post(url, headers=headers, json=payload)
                    # Surface 429 as an exception so the limiter applies backoff;
                    # return the response on any non-429 (incl. 200/400) for inspection.
                    if r.status_code == 429:
                        raise RuntimeError(f"429 {r.text[:120]}")
                    return r

                try:
                    r = await with_rate_limit_retry(
                        _post, context="glm52_probe.raw", model_tier=TIER, max_total_seconds=300,
                    )
                    try:
                        _print(label, _inspect_raw(r.json()))
                    except Exception:
                        print(f"\n[{label}]\n  HTTP {r.status_code} NON-JSON: {r.text[:200]!r}")
                except Exception as exc:  # noqa: BLE001
                    print(f"\n[{label}]\n  EXCEPTION {type(exc).__name__}: {str(exc)[:240]}")

    # ---- Panel-visibility self-check ----
    try:
        from universal_agent.services.zai_observability import _events_path
        p = _events_path()
        hits = []
        if p.exists():
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("model") == MODEL or "glm52_probe" in str(ev.get("caller_fn") or ev.get("caller") or ""):
                    hits.append(ev)
        print("\n########## PANEL-VISIBILITY SELF-CHECK ##########")
        print(f"  events file: {p}")
        print(f"  captured events for this run (model={MODEL} or caller~glm52_probe): {len(hits)}")
        for ev in hits[-8:]:
            print(f"    status={ev.get('status')} cat={ev.get('category')} model={ev.get('model')} "
                  f"caller_fn={ev.get('caller_fn')} in={ev.get('input_tokens')} out={ev.get('output_tokens')}")
        if hits:
            print("  => these rows are what the ZAI Control panel reads. Experiment IS panel-visible. ✅")
        else:
            print("  => no captured rows found — bootstrap/hook may not have installed (investigate).")
    except Exception as exc:  # noqa: BLE001
        print(f"  panel self-check skipped: {type(exc).__name__}: {exc}")

    print("\nDONE.")
    return 0


def main() -> int:
    # Bootstrap MUST run before any client is constructed: it injects the ZAI
    # secrets AND installs the httpx observability monkey-patch.
    from universal_agent.infisical_loader import initialize_runtime_secrets

    res = initialize_runtime_secrets()
    print(f"bootstrap: ok={getattr(res, 'ok', '?')} source={getattr(res, 'source', '?')} "
          f"loaded={getattr(res, 'loaded_count', '?')} env={getattr(res, 'environment', '?')} "
          f"profile={getattr(res, 'deployment_profile', '?')} strict={getattr(res, 'strict_mode', '?')}")
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
