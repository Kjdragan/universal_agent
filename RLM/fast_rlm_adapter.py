from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .types import CorpusBundle, DistillRequest, LaneResult


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
    samples = "\n".join(f"- {doc.path}" for doc in bundle.documents[:12])
    return (
        "You are a recursive research distillation agent.\n"
        f"Topic: {request.topic}\n"
        f"Report title: {request.report_title}\n"
        f"Corpus root: {bundle.source_path}\n"
        f"Estimated tokens: {bundle.estimated_tokens}\n"
        "Sample files:\n"
        f"{samples}\n\n"
        "Instructions:\n"
        "1) Use Python to recursively read markdown/text files under the corpus root.\n"
        "2) Extract the highest-value facts and claims grounded in source snippets.\n"
        "3) Return JSON only with this schema:\n"
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
        "4) Include at least 8 evidence items from multiple files.\n"
        "5) Do not include markdown fences or any extra prose."
    )


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

    query = _build_query(bundle, request)
    response_obj = fast_rlm.run(query, prefix="ua_rlm_eval", verbose=False)

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
