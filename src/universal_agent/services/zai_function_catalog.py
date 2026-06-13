"""ZAI function/stage catalog — pre-built natural-language descriptions of the
LLM call-sites ("stages") that show up in the per-process token panel.

WHY THIS EXISTS (operator design, 2026-06-13): the token panel attributes ZAI
spend to a ``caller_fn`` = ``file::function`` stage, but a function name like
``_detect_clusters_llm_async`` doesn't tell the operator *what that stage does*
or *whether its model tier is justified*. Rather than make the running product
call an LLM to explain each stage (a recurring ZAI/Anthropic tax we explicitly
do NOT want), we PRE-BUILD the explanations once — in an interactive Claude
session that reads the code — and store them as a committed JSON lookup. The
product then does a pure-Python dict lookup. No runtime LLM.

STALENESS: each entry stores a ``source_sha`` (hash of the described function's
source). ``annotate_stale`` recomputes the current hash and flags drift, so a
refactored function whose description has rotted is surfaced for re-description
(instead of silently lying). COVERAGE: ``coverage`` reports observed stages that
have no catalog entry yet — the "N stages undescribed" signal that drives the
occasional re-population pass.

Everything here is fail-soft and pure-Python — a missing/`malformed catalog must
never break the dashboard.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _catalog_path() -> Path:
    override = os.getenv("UA_ZAI_FUNCTION_CATALOG_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "zai_function_catalog.json"


def _repo_src_root() -> Path:
    # .../src/universal_agent/services/zai_function_catalog.py → .../src
    return Path(__file__).resolve().parents[2]


def _resolve_source_path(file_rel: str) -> Optional[Path]:
    """Resolve a `caller` file string to its on-disk path, handling BOTH repo
    layouts the stack-walk produces: src-package files (``universal_agent/...``
    live at ``<repo>/src/universal_agent/...``) and repo-root packages like
    ``discord_intelligence`` (the caller string carries a ``universal_agent/``
    prefix because the REPO directory is named ``universal_agent`` — those live
    at ``<repo>/discord_intelligence/...``). Returns None if neither exists."""
    src_root = _repo_src_root()
    candidates = [src_root / file_rel]
    if file_rel.startswith("universal_agent/"):
        stripped = file_rel[len("universal_agent/"):]
        candidates.append(src_root.parent / stripped)  # repo-root package
    for c in candidates:
        if c.exists():
            return c
    return None


def load_catalog() -> dict[str, Any]:
    """Load the committed catalog JSON. Fail-soft to an empty catalog."""
    try:
        path = _catalog_path()
        if not path.exists():
            return {"version": 0, "entries": {}}
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            return {"version": 0, "entries": {}}
        return data
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_function_catalog load failed: %s", exc)
        return {"version": 0, "entries": {}}


def lookup(caller_fn: str, catalog: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Return the catalog entry for a ``file::function`` stage key, or None.
    Falls back to a file-level entry (key == the bare file) when the exact
    stage isn't described but the file is."""
    cat = catalog if catalog is not None else load_catalog()
    entries = cat.get("entries", {})
    if caller_fn in entries:
        return entries[caller_fn]
    file_part = caller_fn.split("::", 1)[0]
    if file_part in entries:
        return entries[file_part]
    return None


def function_source_hash(caller_fn: str) -> Optional[str]:
    """Short sha256 of the described function's SOURCE, for staleness detection.

    ``caller_fn`` is ``universal_agent/...py::func``. Resolves the file under the
    repo ``src/`` tree, AST-parses it, finds the (possibly nested) def named
    ``func``, and hashes its source segment. None on any problem (file gone,
    func renamed, parse error) — callers treat None as "can't verify"."""
    try:
        if "::" not in caller_fn:
            return None
        file_rel, func = caller_fn.split("::", 1)
        path = _resolve_source_path(file_rel)
        if path is None:
            return None
        source = path.read_text()
        tree = ast.parse(source)
        target = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
                target = node
                break
        if target is None:
            return None
        segment = ast.get_source_segment(source, target)
        if not segment:
            return None
        return hashlib.sha256(segment.encode("utf-8")).hexdigest()[:16]
    except Exception as exc:  # noqa: BLE001
        logger.debug("zai_function_catalog hash failed for %s: %s", caller_fn, exc)
        return None


def annotate_stale(catalog: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Return ``{key: {..entry, 'stale': bool}}`` — stale=True when the entry's
    stored ``source_sha`` no longer matches the function's current source hash.
    File-level entries (no ``::``) are never hashed (stale=False)."""
    cat = catalog if catalog is not None else load_catalog()
    out: dict[str, Any] = {}
    for key, entry in cat.get("entries", {}).items():
        stale = False
        if "::" in key and isinstance(entry, dict):
            stored = entry.get("source_sha")
            if stored:
                current = function_source_hash(key)
                stale = bool(current and current != stored)
        out[key] = {**entry, "stale": stale} if isinstance(entry, dict) else entry
    return out


def _is_describable_stage(caller_fn: str) -> bool:
    """A coverage-eligible stage is a real ``<file.py>::<function>`` key. This
    excludes (a) legacy file-level keys (no ``::``) emitted by events captured
    before the ``caller_fn`` upgrade — they age out of the JSONL in ~6 days and
    were never describable stages; and (b) ``<string>`` / exec-frame callers
    (no real source file) whose function source can't be hashed or described.
    Without this filter the "N stages undescribed" signal is inflated by
    un-catalogable noise during the transition window."""
    if "::" not in caller_fn:
        return False
    file_part = caller_fn.split("::", 1)[0]
    return file_part.endswith(".py")


def coverage(observed_caller_fns: list[str], catalog: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Coverage of observed STAGES against the catalog. ``undescribed`` is the
    list of real ``file.py::function`` stages with no entry (exact or file-level)
    — the "N stages need describing" signal for the re-population pass. Non-stage
    keys (legacy file-level events, ``<string>`` exec frames) are ignored
    (`_is_describable_stage`)."""
    cat = catalog if catalog is not None else load_catalog()
    described: set[str] = set()
    undescribed: set[str] = set()
    for cf in set(observed_caller_fns):  # unique stages, not occurrences
        if not _is_describable_stage(cf):
            continue
        if lookup(cf, cat) is not None:
            described.add(cf)
        else:
            undescribed.add(cf)
    return {
        "described_count": len(described),
        "undescribed_count": len(undescribed),
        "undescribed": sorted(undescribed),
    }
