from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import CorpusBundle, DistillRequest, LaneResult


def _extract_section(md: str, heading: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", flags=re.MULTILINE)
    match = pattern.search(md)
    if not match:
        return ""
    start = match.end()
    next_h = re.search(r"^##\s+", md[start:], flags=re.MULTILINE)
    end = start + next_h.start() if next_h else len(md)
    return md[start:end].strip()


def parse_summary_and_findings(report_md: str) -> tuple[str, list[str]]:
    summary = _extract_section(report_md, "Executive Summary")
    findings_block = _extract_section(report_md, "Key Findings")
    findings: list[str] = []
    for line in findings_block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            findings.append(stripped[2:].strip())
    return summary, findings


def _fallback_findings(evidence_items: list[dict[str, Any]], limit: int = 8) -> list[str]:
    seen = set()
    findings: list[str] = []
    for item in evidence_items:
        snippet = str(item.get("snippet", "")).strip()
        if not snippet:
            continue
        normalized = " ".join(snippet.split())
        if normalized in seen:
            continue
        seen.add(normalized)
        findings.append(normalized[:280])
        if len(findings) >= limit:
            break
    return findings


def write_contract(
    request: DistillRequest,
    corpus: CorpusBundle,
    lane_result: LaneResult,
    run_dir: Path,
) -> dict[str, str]:
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = lane_result.executive_summary.strip()
    findings = [item.strip() for item in lane_result.key_findings if item.strip()]

    if not summary and lane_result.raw_artifacts.get("report_md"):
        report_path = Path(lane_result.raw_artifacts["report_md"])
        if report_path.exists():
            report_md = report_path.read_text(encoding="utf-8", errors="ignore")
            parsed_summary, parsed_findings = parse_summary_and_findings(report_md)
            if parsed_summary:
                summary = parsed_summary
            if parsed_findings:
                findings = parsed_findings

    if not findings:
        findings = _fallback_findings(lane_result.evidence_items)

    key_takeaways_md_path = run_dir / "key_takeaways.md"
    key_takeaways_json_path = run_dir / "key_takeaways.json"
    evidence_index_path = run_dir / "evidence_index.jsonl"
    run_metadata_path = run_dir / "run_metadata.json"

    md_lines = ["# Key Takeaways", ""]
    md_lines.append("## Executive Summary")
    md_lines.append(summary if summary else "No executive summary extracted.")
    md_lines.append("")
    md_lines.append("## Key Findings")
    if findings:
        for finding in findings:
            md_lines.append(f"- {finding}")
    else:
        md_lines.append("- No key findings extracted.")
    md_lines.append("")

    key_takeaways_md_path.write_text("\n".join(md_lines), encoding="utf-8")

    key_takeaways_json = {
        "mode": request.mode,
        "topic": request.topic,
        "report_title": request.report_title,
        "executive_summary": summary,
        "key_findings": findings,
    }
    key_takeaways_json_path.write_text(
        json.dumps(key_takeaways_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with evidence_index_path.open("w", encoding="utf-8") as handle:
        for item in lane_result.evidence_items:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": request.mode,
        "topic": request.topic,
        "report_title": request.report_title,
        "source": str(corpus.source_path),
        "threshold_tokens": request.threshold_tokens,
        "enforce_threshold": request.enforce_threshold,
        "estimated_tokens": corpus.estimated_tokens,
        "total_chars": corpus.total_chars,
        "total_words": corpus.total_words,
        "document_count": len(corpus.documents),
        "lane_dir": str(lane_result.lane_dir),
        "raw_artifacts": lane_result.raw_artifacts,
        "evidence_count": len(lane_result.evidence_items),
    }
    run_metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "key_takeaways_md": str(key_takeaways_md_path),
        "key_takeaways_json": str(key_takeaways_json_path),
        "evidence_index_jsonl": str(evidence_index_path),
        "run_metadata_json": str(run_metadata_path),
    }
