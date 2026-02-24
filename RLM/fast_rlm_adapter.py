from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import CorpusBundle, DistillRequest, LaneResult


def _is_debug_enabled() -> bool:
    return os.environ.get("RLM_DEBUG", "0") in {"1", "true", "TRUE", "yes", "on"}


def _debug(message: str) -> None:
    if not _is_debug_enabled():
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[RLM DEBUG {stamp}] {message}", flush=True)


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(
            line for line in cleaned.splitlines() if not line.strip().startswith("```")
        ).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _build_query(bundle: CorpusBundle, request: DistillRequest) -> str:
    corpus_texts = []
    for doc in bundle.documents:
        try:
            text = doc.path.read_text(encoding="utf-8", errors="replace")
            corpus_texts.append(f"--- BEGIN FILE: {doc.rel_path} ---\n{text}\n--- END FILE: {doc.rel_path} ---\n")
        except Exception as exc:
            _debug(f"Warning: skipped {doc.rel_path} during injection: {exc}")

    full_text = "\n".join(corpus_texts)

    return (
        "You are a recursive research distillation agent.\n"
        f"Topic: {request.topic}\n"
        f"Report title: {request.report_title}\n"
        f"Estimated tokens: {bundle.estimated_tokens}\n"
        "Instructions:\n"
        "1) Read the provided corpus text below.\n"
        "2) Extract the highest-value facts and claims grounded in the source snippets.\n"
        "3) Write Python to analyze or structure the data if helpful, but ultimately return JSON only with this schema:\n"
        "{\n"
        '  "executive_summary": "string",\n'
        '  "key_findings": ["string", "..."],\n'
        '  "evidence": [\n'
        "    {\n"
        '      "claim": "string",\n'
        '      "snippet": "string",\n'
        '      "source_path": "string",\n'
        '      "date": "string",\n'
        '      "notes": "string"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "4) Include at least 8 evidence items.\n"
        "5) Do not include markdown fences or any extra prose.\n\n"
        "=== CORPUS ===\n"
        f"{full_text}"
    )


def _prepare_fast_rlm_runtime_env() -> dict[str, Any]:
    deno_bin = Path.home() / ".deno" / "bin"
    current_path = os.environ.get("PATH", "")
    if deno_bin.exists() and str(deno_bin) not in current_path.split(":"):
        os.environ["PATH"] = f"{deno_bin}:{current_path}" if current_path else str(deno_bin)

    if not os.environ.get("RLM_MODEL_API_KEY"):
        for key_name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ZAI_API_KEY"):
            candidate = os.environ.get(key_name)
            if candidate:
                os.environ["RLM_MODEL_API_KEY"] = candidate
                break

    if not os.environ.get("RLM_MODEL_BASE_URL"):
        base_url = os.environ.get("OPENAI_BASE_URL")
        if base_url:
            # fast-rlm expects an OpenAI-compatible endpoint; for ZAI we prefer
            # the coding/paas path when a generic paas URL is provided.
            if base_url.endswith("/api/paas/v4"):
                base_url = base_url.replace("/api/paas/v4", "/api/coding/paas/v4")
            os.environ["RLM_MODEL_BASE_URL"] = base_url

    if not os.environ.get("RLM_MODEL_API_KEY"):
        raise RuntimeError(
            "fast-rlm requires RLM_MODEL_API_KEY (or OPENAI_API_KEY / ANTHROPIC_API_KEY / "
            "ANTHROPIC_AUTH_TOKEN / ZAI_API_KEY in env)."
        )

    primary_model = (
        os.environ.get("RLM_PRIMARY_MODEL")
        or os.environ.get("OPENAI_DEFAULT_MODEL")
        or os.environ.get("MODEL_NAME")
        or "glm-5"
    )
    sub_model = (
        os.environ.get("RLM_SUB_MODEL")
        or os.environ.get("ANTHROPIC_DEFAULT_SONNET_MODEL")
        or primary_model
    )

    runtime = {
        "primary_agent": primary_model,
        "sub_agent": sub_model,
    }
    _debug(
        "fast-rlm runtime env prepared "
        f"base_url={os.environ.get('RLM_MODEL_BASE_URL', '<unset>')} "
        f"api_key_set={bool(os.environ.get('RLM_MODEL_API_KEY'))} "
        f"primary_model={primary_model} sub_model={sub_model}"
    )
    return runtime


def run_fast_rlm_adapter(request: DistillRequest, bundle: CorpusBundle, run_dir: Path) -> LaneResult:
    lane_dir = run_dir / "fast_rlm_adapter"
    lane_dir.mkdir(parents=True, exist_ok=True)

    try:
        import fast_rlm  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "fast_rlm is not available in this environment. "
            "Install/configure upstream fast-rlm first, then retry."
        ) from exc

    runtime_config = _prepare_fast_rlm_runtime_env()
    query = _build_query(bundle, request)
    _debug(
        "invoking fast_rlm.run "
        f"source={bundle.source_path} tokens_est={bundle.estimated_tokens} "
        f"config={runtime_config}"
    )
    response_obj = fast_rlm.run(
        query,
        prefix="ua_rlm_eval",
        config=runtime_config,
        verbose=False,
    )
    _debug(f"fast_rlm.run returned type={type(response_obj).__name__}")

    raw_response_path = lane_dir / "fast_rlm_raw_response.json"
    raw_response_path.write_text(
        json.dumps(response_obj, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    usage: dict[str, Any] = {}
    text_result = ""
    parsed: dict[str, Any] | None = None

    if isinstance(response_obj, dict):
        usage_raw = response_obj.get("usage")
        if isinstance(usage_raw, dict):
            usage = usage_raw

        candidate = response_obj.get("result")
        if isinstance(candidate, dict):
            parsed = candidate
        elif isinstance(candidate, str):
            text_result = candidate

        if not parsed and not text_result:
            # Some builds may return direct text under alternate keys.
            for key in ("output", "text", "final"):
                value = response_obj.get(key)
                if isinstance(value, str):
                    text_result = value
                    break
    elif isinstance(response_obj, str):
        text_result = response_obj

    if not parsed and text_result:
        parsed = _extract_json(text_result)

    if not parsed:
        raise RuntimeError(
            "fast_rlm response was not parseable into expected JSON schema. "
            f"Inspect: {raw_response_path}"
        )

    executive_summary = str(parsed.get("executive_summary", "")).strip()

    findings = parsed.get("key_findings", [])
    key_findings = [str(item).strip() for item in findings if str(item).strip()] if isinstance(findings, list) else []

    evidence_raw = parsed.get("evidence", [])
    evidence_items: list[dict[str, Any]] = []
    if isinstance(evidence_raw, list):
        for item in evidence_raw:
            if not isinstance(item, dict):
                continue
            evidence_items.append(
                {
                    "claim": str(item.get("claim", "")).strip(),
                    "snippet": str(item.get("snippet", "")).strip(),
                    "source_path": str(item.get("source_path", "")).strip(),
                    "source_url": "",
                    "date": str(item.get("date", "unknown")).strip(),
                    "notes": str(item.get("notes", "")).strip(),
                }
            )

    usage_path = lane_dir / "usage.json"
    usage_path.write_text(json.dumps(usage, ensure_ascii=False, indent=2), encoding="utf-8")

    return LaneResult(
        mode="fast_rlm_adapter",
        lane_dir=lane_dir,
        raw_artifacts={
            "raw_response_json": str(raw_response_path),
            "usage_json": str(usage_path),
        },
        executive_summary=executive_summary,
        key_findings=key_findings,
        evidence_items=evidence_items,
    )
