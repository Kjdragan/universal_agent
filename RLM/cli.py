from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from RLM.runner import compare_lanes, run_distillation  # type: ignore
    from RLM.session_replay import stage_session_corpus  # type: ignore
    from RLM.types import DistillRequest  # type: ignore
else:
    from .runner import compare_lanes, run_distillation
    from .session_replay import stage_session_corpus
    from .types import DistillRequest


def _build_base_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RLM experimental distillation CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--source", help="Absolute source path (file or directory)")
        target.add_argument("--workspace", help="UA workspace path (used with --task-name)")
        target.add_argument("--task-name", help="UA task name under workspace/tasks/<task-name>")
        target.add_argument("--topic", required=True, help="Research topic")
        target.add_argument("--report-title", required=True, help="Report title")
        target.add_argument(
            "--output-dir",
            default="RLM/work_products",
            help="Output root directory for artifacts",
        )
        target.add_argument(
            "--threshold-tokens",
            type=int,
            default=180_000,
            help="Threshold used to gate large-corpus runs",
        )
        target.add_argument(
            "--enforce-threshold",
            action="store_true",
            help="Fail when estimated tokens are below threshold",
        )
        target.add_argument(
            "--model",
            default="claude-sonnet-4-20250514",
            help="Model name used by ua_rom_baseline lane",
        )

    distill = sub.add_parser("distill", help="Run one lane")
    add_common_args(distill)
    distill.add_argument(
        "--mode",
        required=True,
        choices=["ua_rom_baseline", "fast_rlm_adapter"],
        help="Lane to execute",
    )

    compare = sub.add_parser("compare", help="Run both lanes and write comparison summaries")
    add_common_args(compare)

    stage = sub.add_parser(
        "stage-session",
        help="Copy a completed AGENT_RUN_WORKSPACES session into RLM/corpora for replay",
    )
    stage.add_argument("--session-dir", required=True, help="Absolute path to the source session directory")
    stage.add_argument(
        "--target-root",
        default="RLM/corpora",
        help="Target root directory where the copied replay corpus will be created",
    )

    return parser


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    parser = _build_base_parser()
    args = parser.parse_args()

    if args.command == "distill":
        request = DistillRequest(
            mode=args.mode,
            topic=args.topic,
            report_title=args.report_title,
            source=args.source,
            workspace=args.workspace,
            task_name=args.task_name,
            output_dir=args.output_dir,
            threshold_tokens=args.threshold_tokens,
            enforce_threshold=args.enforce_threshold,
            model=args.model,
        )
        result = run_distillation(request)
        _print_json(
            {
                "status": "ok",
                "mode": request.mode,
                "run_dir": str(result.run_dir),
                "source": str(result.corpus.source_path),
                "estimated_tokens": result.corpus.estimated_tokens,
                "document_count": len(result.corpus.documents),
                "outputs": result.outputs,
                "evidence_count": len(result.lane_result.evidence_items),
            }
        )
        return

    if args.command == "compare":
        base_request = DistillRequest(
            mode="ua_rom_baseline",
            topic=args.topic,
            report_title=args.report_title,
            source=args.source,
            workspace=args.workspace,
            task_name=args.task_name,
            output_dir=args.output_dir,
            threshold_tokens=args.threshold_tokens,
            enforce_threshold=args.enforce_threshold,
            model=args.model,
        )
        payload = compare_lanes(base_request)
        _print_json({"status": "ok", **payload})
        return

    if args.command == "stage-session":
        payload = stage_session_corpus(args.session_dir, args.target_root)
        _print_json({"status": "ok", **payload})
        return

    parser.error(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
