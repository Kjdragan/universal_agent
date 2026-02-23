from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .corpus_adapter import build_corpus_bundle, resolve_source
from .fast_rlm_adapter import run_fast_rlm_adapter
from .output_contract import write_contract
from .types import DistillRequest, DistillResult
from .ua_rom_baseline import run_ua_rom_baseline


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _ensure_threshold(request: DistillRequest, estimated_tokens: int) -> None:
    if request.enforce_threshold and estimated_tokens < request.threshold_tokens:
        raise ValueError(
            f"Corpus estimated tokens ({estimated_tokens}) is below threshold "
            f"({request.threshold_tokens}) while --enforce-threshold is set."
        )


def _run_lane(request: DistillRequest, run_dir: Path, bundle):
    if request.mode == "ua_rom_baseline":
        return run_ua_rom_baseline(request, bundle, run_dir)
    if request.mode == "fast_rlm_adapter":
        return run_fast_rlm_adapter(request, bundle, run_dir)
    raise ValueError(f"Unsupported mode: {request.mode}")


def run_distillation(request: DistillRequest) -> DistillResult:
    source_path = resolve_source(request.source, request.workspace, request.task_name)
    bundle = build_corpus_bundle(source_path)
    _ensure_threshold(request, bundle.estimated_tokens)

    output_root = Path(request.output_dir).expanduser().resolve()
    run_dir = output_root / f"run_{request.mode}_{_utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    lane_result = _run_lane(request, run_dir, bundle)
    outputs = write_contract(request, bundle, lane_result, run_dir)

    return DistillResult(
        request=request,
        corpus=bundle,
        run_dir=run_dir,
        outputs=outputs,
        lane_result=lane_result,
    )


def compare_lanes(base_request: DistillRequest) -> dict[str, str]:
    source_path = resolve_source(base_request.source, base_request.workspace, base_request.task_name)
    bundle = build_corpus_bundle(source_path)
    _ensure_threshold(base_request, bundle.estimated_tokens)

    output_root = Path(base_request.output_dir).expanduser().resolve()
    run_dir = output_root / f"comparison_{_utc_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, DistillResult] = {}
    lane_errors: dict[str, str] = {}

    for mode in ("ua_rom_baseline", "fast_rlm_adapter"):
        request = DistillRequest(
            mode=mode,
            topic=base_request.topic,
            report_title=base_request.report_title,
            source=base_request.source,
            workspace=base_request.workspace,
            task_name=base_request.task_name,
            output_dir=str(run_dir),
            threshold_tokens=base_request.threshold_tokens,
            enforce_threshold=False,
            model=base_request.model,
        )
        try:
            lane_result = _run_lane(request, run_dir, bundle)
            outputs = write_contract(request, bundle, lane_result, lane_result.lane_dir)
            results[mode] = DistillResult(
                request=request,
                corpus=bundle,
                run_dir=lane_result.lane_dir,
                outputs=outputs,
                lane_result=lane_result,
            )
        except Exception as exc:
            lane_errors[mode] = str(exc)

    summary = {
        "source": str(bundle.source_path),
        "estimated_tokens": bundle.estimated_tokens,
        "document_count": len(bundle.documents),
        "lane_errors": lane_errors,
        "lanes": {
            mode: {
                "run_dir": str(result.run_dir),
                "evidence_count": len(result.lane_result.evidence_items),
                "key_takeaways": result.outputs.get("key_takeaways_md"),
                "metadata": result.outputs.get("run_metadata_json"),
            }
            for mode, result in results.items()
        },
    }

    summary_json = run_dir / "comparison_summary.json"
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# RLM Lane Comparison",
        "",
        f"- Source: `{bundle.source_path}`",
        f"- Estimated tokens: `{bundle.estimated_tokens}`",
        f"- Documents: `{len(bundle.documents)}`",
        "",
        "## Lane outputs",
    ]

    if lane_errors:
        lines.extend(["", "## Lane errors"])
        for mode, message in lane_errors.items():
            lines.append(f"- **{mode}**: {message}")
        lines.append("")

    for mode, result in results.items():
        lines.extend(
            [
                f"### {mode}",
                f"- Run dir: `{result.run_dir}`",
                f"- Evidence items: `{len(result.lane_result.evidence_items)}`",
                f"- Key takeaways: `{result.outputs.get('key_takeaways_md')}`",
                f"- Metadata: `{result.outputs.get('run_metadata_json')}`",
                "",
            ]
        )

    summary_md = run_dir / "comparison_summary.md"
    summary_md.write_text("\n".join(lines), encoding="utf-8")

    return {
        "comparison_dir": str(run_dir),
        "comparison_summary_json": str(summary_json),
        "comparison_summary_md": str(summary_md),
    }
