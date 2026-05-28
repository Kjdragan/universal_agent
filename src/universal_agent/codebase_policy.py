"""Codebase mutation gating for VP missions and agent subprocesses.

This module answers a single question in three forms:

1. "Is this filesystem path inside an approved Universal Agent checkout?"
   (:func:`path_is_within_roots`, :func:`is_approved_codebase_path`,
   :func:`validate_codebase_root`)
2. "Is this agent allowed to mutate that checkout right now?"
   (:func:`agent_can_mutate_codebase`)
3. "Given a stored policy dict and a per-request override payload, what
   access does this specific mission actually have?"
   (:func:`build_codebase_access`, :func:`normalize_codebase_access`,
   :func:`resolve_codebase_access`)

Callers in `gateway_server`, the VP dispatcher / Claude clients, `hooks`,
`session_policy`, and the `proactive_codie` service rely on these helpers
to keep VP-spawned subprocesses from editing directories outside the
operator-approved allowlist (``UA_APPROVED_CODEBASE_ROOTS`` env, or the
detected VPS roots in :data:`_KNOWN_VPS_CODEBASE_ROOTS`).

All functions in this module are pure: they read environment variables
and resolve paths against the filesystem, but they perform no mutations
and no I/O beyond ``Path.resolve()`` / ``Path.exists()``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Sequence

#: Canonical production checkout on the VPS. Always seeded into the
#: approved-root allowlist even when nothing is configured.
DEFAULT_APPROVED_CODEBASE_ROOT = "/opt/universal_agent"

#: Other VPS checkout locations that are added to the allowlist when they
#: exist on disk. Lets staging / mirror clones be picked up without
#: explicit configuration, but never invents non-existent paths.
_KNOWN_VPS_CODEBASE_ROOTS = (
    DEFAULT_APPROVED_CODEBASE_ROOT,
    "/opt/universal-agent-staging",
    "/opt/universal_agent_repo",
)

#: Default agent names allowed to mutate an approved codebase root.
#: Overridable per-policy via ``mutation_agents``.
DEFAULT_MUTATION_AGENTS = ("simone", "code-writer", "vp.coder.primary")


def _truthy(value: Any, *, default: bool = False) -> bool:
    """Coerce ``value`` to ``bool`` with permissive env-style parsing.

    Accepts native booleans and numerics as-is, treats common string
    spellings (``"1"``, ``"true"``, ``"yes"``, ``"on"`` and their inverses,
    case-insensitive) as their boolean values, and returns ``default`` for
    empty input or unrecognised strings. Used so policy dicts that came
    from env vars, JSON, or hand-written YAML all coerce identically.
    """
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
    """Resolve each path and return a deduplicated list preserving order.

    Empty / whitespace-only entries are dropped. Tildes are expanded and
    each path is fully ``resolve()``-d so two callers passing the same
    directory through different spellings (relative vs absolute,
    symlinked vs canonical) collapse to a single allowlist entry.
    """
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
    """Lowercase, strip, and dedupe agent name strings (order-preserving).

    Agent name comparisons throughout this module are case-insensitive,
    so policies built from mixed-case input still match the lowercase
    actor name passed to :func:`agent_can_mutate_codebase`.
    """
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
    """Read the operator-approved codebase roots, with sensible defaults.

    Resolution order:

    1. ``UA_APPROVED_CODEBASE_ROOTS`` env var (comma-separated list), if
       set. This is the override hook.
    2. Otherwise, seed with :data:`DEFAULT_APPROVED_CODEBASE_ROOT` plus
       any other entry in :data:`_KNOWN_VPS_CODEBASE_ROOTS` that actually
       exists on disk. This keeps dev boxes / staging mirrors out of the
       allowlist on machines where they aren't present.

    All entries pass through :func:`_dedupe_paths` so the returned list
    is fully resolved and free of duplicates.
    """
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
    """Assert ``codebase_root`` lies inside the allowlist; return the resolved path.

    Used at the boundary where an untrusted request (mission metadata,
    request payload) supplies a codebase path. Raises ``ValueError`` if
    the resolved path is not within any approved root, so callers fail
    loudly rather than silently routing to the wrong tree.

    ``approved_roots`` defaults to :func:`approved_codebase_roots_from_env`
    when not supplied, but callers can pass an explicit list to validate
    against a tighter set (e.g. only the roots already in a policy dict).
    """
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
    """Construct a fresh codebase-access policy dict.

    The returned dict has the canonical shape consumed elsewhere in this
    module: ``{"enabled": bool, "roots": list[str], "mutation_agents":
    list[str]}``. ``enabled`` is intentionally forced to ``False`` when
    the resolved root list is empty — having access enabled with no
    allowed roots would be a silent footgun for downstream gating.

    If ``codebase_root`` is supplied it is validated against
    ``roots`` (if any) or the env allowlist, and appended to ``roots``
    when not already present.
    """
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
    """Coerce a stored / partially-populated policy dict to canonical shape.

    Tolerates ``None``, missing keys, mixed-case agent names, duplicate
    or unresolved root paths, and env-style truthy strings for
    ``enabled``. Pulls ``roots`` from
    :func:`approved_codebase_roots_from_env` when the incoming policy
    has none. Validates and appends ``codebase_root`` if present.
    The output is safe to pass to :func:`resolve_codebase_access`.
    """
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
    """Merge a stored ``policy`` with per-request overrides into a final access dict.

    Used at mission-claim time: ``policy`` is the persisted default for
    this agent / source_kind and ``request_metadata`` is the inbound
    task payload. The two are combined so an explicit ``codebase_root``
    on the request is appended to the allowlist (after validation),
    and ``repo_mutation_allowed`` on the request can flip ``enabled``
    on or off relative to the policy default.

    The returned dict adds a ``codebase_root`` key beyond the shape
    returned by :func:`build_codebase_access` — set to the explicit
    request root if any, otherwise the first entry in ``roots``, else
    ``None``. Downstream callers (dispatcher, Claude clients) use that
    field as the working directory for the spawned subprocess.
    """
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
    """Return ``True`` iff ``agent_name`` may mutate the repo under ``access``.

    Three things must all be true: (1) ``access`` is a dict with
    ``enabled=True``, (2) ``agent_name`` is non-empty, and (3) the
    lowercase agent name appears in ``access["mutation_agents"]``.
    Anything else — ``None`` access, disabled policy, missing name,
    name not on the allowlist — returns ``False``. Comparison is
    case-insensitive.
    """
    if not isinstance(access, dict) or not access.get("enabled"):
        return False
    actor = str(agent_name or "").strip().lower()
    if not actor:
        return False
    allowed = {str(item or "").strip().lower() for item in access.get("mutation_agents") or []}
    return actor in allowed


def path_is_within_roots(path_value: str | Path, roots: Sequence[str | Path]) -> bool:
    """Return ``True`` iff ``path_value`` resolves under any directory in ``roots``.

    Safe to call with arbitrary string input — anything that fails to
    resolve (NUL bytes, malformed paths) returns ``False`` rather than
    raising, so this can be used as a predicate at trust boundaries
    without a surrounding try/except.
    """
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
    """Convenience: ``path_is_within_roots`` against the env allowlist.

    Equivalent to ``path_is_within_roots(path_value,
    approved_codebase_roots_from_env())``. Use this when the caller has
    no policy dict in hand and just needs to ask "is this path one we
    can possibly mutate today?".
    """
    return path_is_within_roots(path_value, approved_codebase_roots_from_env())


def repo_mutation_requested(payload: dict[str, Any] | None) -> bool:
    """Return ``True`` iff this request payload is asking for repo mutation.

    Two signals count: an explicit truthy ``repo_mutation_allowed`` flag,
    or a ``workflow_kind`` / ``mission_type`` of ``"code_change"`` /
    ``"coding_task"``. Tolerates non-dict input (returns ``False``) so
    callers can hand in raw task hub rows without pre-validating shape.
    """
    if not isinstance(payload, dict):
        return False
    if _truthy(payload.get("repo_mutation_allowed"), default=False):
        return True
    workflow_kind = str(payload.get("workflow_kind") or payload.get("mission_type") or "").strip().lower()
    return workflow_kind in {"code_change", "coding_task"}
