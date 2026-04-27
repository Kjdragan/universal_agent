from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Sequence

DEFAULT_APPROVED_CODEBASE_ROOT = "/opt/universal_agent"
_KNOWN_VPS_CODEBASE_ROOTS = (
    DEFAULT_APPROVED_CODEBASE_ROOT,
    "/opt/universal-agent-staging",
    "/opt/universal_agent_repo",
)
DEFAULT_MUTATION_AGENTS = ("simone", "code-writer", "vp.coder.primary")


def _truthy(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _dedupe_paths(paths: Iterable[str | Path]) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for value in paths:
        raw = str(value or "").strip()
        if not raw:
            continue
        candidate = str(Path(raw).expanduser().resolve())
        if candidate in seen:
            continue
        seen.add(candidate)
        resolved.append(candidate)
    return resolved


def _dedupe_agents(values: Iterable[str]) -> list[str]:
    resolved: list[str] = []
    seen: set[str] = set()
    for value in values:
        agent = str(value or "").strip().lower()
        if not agent or agent in seen:
            continue
        seen.add(agent)
        resolved.append(agent)
    return resolved


def approved_codebase_roots_from_env() -> list[str]:
    raw = str(os.getenv("UA_APPROVED_CODEBASE_ROOTS") or "").strip()
    if raw:
        return _dedupe_paths(item for item in raw.split(","))

    seeded: list[str] = [DEFAULT_APPROVED_CODEBASE_ROOT]
    for candidate in _KNOWN_VPS_CODEBASE_ROOTS[1:]:
        path = Path(candidate).expanduser()
        if path.exists():
            seeded.append(str(path))
    return _dedupe_paths(seeded)


def validate_codebase_root(
    codebase_root: str,
    *,
    approved_roots: Sequence[str | Path] | None = None,
) -> str:
    candidate = Path(str(codebase_root or "").strip()).expanduser().resolve()
    allowlist = _dedupe_paths(approved_roots or approved_codebase_roots_from_env())
    for root in allowlist:
        root_path = Path(root)
        try:
            candidate.relative_to(root_path)
            return str(candidate)
        except ValueError:
            continue
    raise ValueError(
        f"Codebase root '{candidate}' is not within approved roots: {', '.join(allowlist) or '(none)'}"
    )


def build_codebase_access(
    *,
    enabled: bool = False,
    roots: Sequence[str | Path] | None = None,
    mutation_agents: Sequence[str] | None = None,
    codebase_root: str | None = None,
) -> dict[str, Any]:
    resolved_roots = _dedupe_paths(roots or ())
    if codebase_root:
        validated = validate_codebase_root(codebase_root, approved_roots=resolved_roots or None)
        if validated not in resolved_roots:
            resolved_roots.append(validated)
    return {
        "enabled": bool(enabled and resolved_roots),
        "roots": resolved_roots,
        "mutation_agents": _dedupe_agents(mutation_agents or DEFAULT_MUTATION_AGENTS),
    }


def normalize_codebase_access(policy: dict[str, Any] | None) -> dict[str, Any]:
    incoming = policy if isinstance(policy, dict) else {}
    resolved_roots = _dedupe_paths(incoming.get("roots") or approved_codebase_roots_from_env())
    explicit_root = str(incoming.get("codebase_root") or "").strip()
    if explicit_root:
        validated = validate_codebase_root(explicit_root, approved_roots=resolved_roots or None)
        if validated not in resolved_roots:
            resolved_roots.append(validated)
    enabled = _truthy(incoming.get("enabled"), default=False)
    mutation_agents = _dedupe_agents(incoming.get("mutation_agents") or DEFAULT_MUTATION_AGENTS)
    return {
        "enabled": bool(enabled and resolved_roots),
        "roots": resolved_roots,
        "mutation_agents": mutation_agents,
    }


def resolve_codebase_access(
    *,
    policy: dict[str, Any] | None = None,
    request_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_codebase_access(policy)
    metadata = request_metadata if isinstance(request_metadata, dict) else {}
    explicit_root = str(metadata.get("codebase_root") or "").strip()
    if explicit_root:
        validated = validate_codebase_root(explicit_root, approved_roots=normalized["roots"] or None)
        if validated not in normalized["roots"]:
            normalized["roots"] = list(normalized["roots"]) + [validated]
    if "repo_mutation_allowed" in metadata:
        normalized["enabled"] = bool(
            _truthy(metadata.get("repo_mutation_allowed"), default=normalized["enabled"])
            and normalized["roots"]
        )
    normalized["codebase_root"] = explicit_root or (normalized["roots"][0] if normalized["roots"] else None)
    return normalized


def agent_can_mutate_codebase(agent_name: str | None, access: dict[str, Any] | None) -> bool:
    if not isinstance(access, dict) or not access.get("enabled"):
        return False
    actor = str(agent_name or "").strip().lower()
    if not actor:
        return False
    allowed = {str(item or "").strip().lower() for item in access.get("mutation_agents") or []}
    return actor in allowed


def path_is_within_roots(path_value: str | Path, roots: Sequence[str | Path]) -> bool:
    try:
        candidate = Path(path_value).expanduser().resolve()
    except Exception:
        return False
    for root in _dedupe_paths(roots):
        try:
            candidate.relative_to(Path(root))
            return True
        except ValueError:
            continue
    return False


def is_approved_codebase_path(path_value: str | Path) -> bool:
    return path_is_within_roots(path_value, approved_codebase_roots_from_env())


def repo_mutation_requested(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if _truthy(payload.get("repo_mutation_allowed"), default=False):
        return True
    workflow_kind = str(payload.get("workflow_kind") or payload.get("mission_type") or "").strip().lower()
    return workflow_kind in {"code_change", "coding_task"}
