from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4


def _json_default(value: Any) -> str:
    return str(value)


def _format_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=True, default=_json_default)


def render_markdown_snapshot(snapshot: dict[str, Any]) -> str:
    supervisor_id = str(snapshot.get("supervisor_id") or "supervisor")
    generated_at = str(snapshot.get("generated_at") or "")
    summary = str(snapshot.get("summary") or "")
    severity = str(snapshot.get("severity") or "info")
    kpis = snapshot.get("kpis") if isinstance(snapshot.get("kpis"), dict) else {}
    diagnostics = snapshot.get("diagnostics") if isinstance(snapshot.get("diagnostics"), dict) else {}
    recommendations = snapshot.get("recommendations") if isinstance(snapshot.get("recommendations"), list) else []

    lines: list[str] = []
    lines.append(f"# Supervisor Brief: {supervisor_id}")
    lines.append("")
    lines.append(f"- Generated: `{generated_at}`")
    lines.append(f"- Severity: `{severity}`")
    if summary:
        lines.append(f"- Summary: {summary}")
    lines.append("")

    lines.append("## KPI Snapshot")
    if kpis:
        for key, value in kpis.items():
            lines.append(f"- `{key}`: `{value}`")
    else:
        lines.append("- No KPI values available.")
    lines.append("")

    lines.append("## Diagnostics")
    lines.append("```json")
    lines.append(_format_json(diagnostics))
    lines.append("```")
    lines.append("")

    lines.append("## Recommendations")
    if recommendations:
        for idx, rec in enumerate(recommendations, start=1):
            if not isinstance(rec, dict):
                continue
            lines.append(f"{idx}. {rec.get('action')}")
            rationale = str(rec.get("rationale") or "").strip()
            endpoint = str(rec.get("endpoint_or_command") or "").strip()
            if rationale:
                lines.append(f"   - Rationale: {rationale}")
            if endpoint:
                lines.append(f"   - Endpoint/Command: `{endpoint}`")
            lines.append(
                "   - Requires confirmation: "
                + ("yes" if bool(rec.get("requires_confirmation")) else "no")
            )
    else:
        lines.append("1. No immediate recommendations.")
    lines.append("")

    lines.append("## Machine Readable")
    lines.append("```json")
    lines.append(_format_json(snapshot))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def persist_snapshot(
    *,
    supervisor_id: str,
    snapshot: dict[str, Any],
    artifacts_root: Path,
) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    date_slug = now.strftime("%Y-%m-%d")
    ts_slug = now.strftime("%Y%m%dT%H%M%SZ")
    nonce = uuid4().hex[:8]

    root = artifacts_root / "supervisor-briefs" / str(supervisor_id).strip() / date_slug
    root.mkdir(parents=True, exist_ok=True)

    stem = f"{ts_slug}_{nonce}"
    md_path = root / f"{stem}.md"
    json_path = root / f"{stem}.json"

    md_path.write_text(render_markdown_snapshot(snapshot), encoding="utf-8")
    json_path.write_text(_format_json(snapshot), encoding="utf-8")

    return {
        "markdown_path": str(md_path),
        "json_path": str(json_path),
    }


def list_snapshot_runs(
    *,
    supervisor_id: str,
    artifacts_root: Path,
    limit: int = 25,
) -> list[dict[str, Any]]:
    root = artifacts_root / "supervisor-briefs" / str(supervisor_id).strip()
    if not root.exists() or not root.is_dir():
        return []

    candidates: list[Path] = []
    for file_path in root.rglob("*.json"):
        if file_path.is_file():
            candidates.append(file_path)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    runs: list[dict[str, Any]] = []
    for json_path in candidates[: max(1, min(int(limit), 200))]:
        payload: dict[str, Any] = {}
        try:
            parsed = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}
        md_path = json_path.with_suffix(".md")
        runs.append(
            {
                "supervisor_id": str(supervisor_id),
                "generated_at": payload.get("generated_at") or datetime.fromtimestamp(json_path.stat().st_mtime, timezone.utc).isoformat(),
                "severity": payload.get("severity") or "info",
                "summary": payload.get("summary") or "",
                "artifacts": {
                    "markdown_path": str(md_path) if md_path.exists() else "",
                    "json_path": str(json_path),
                },
            }
        )
    return runs
