"""Intelligence lane configuration loader.

Reads `config/intel_lanes.yaml` (shipped with the package) and exposes
typed lane configs for the proactive intelligence pipeline.

This module is intentionally small in v2 PR 11 — it only provides the
schema and loader. Existing `claude_code_intel.py` paths are NOT yet
wired to read from here; that's a follow-up generalization PR. Tests
verify the YAML parses, all known lane keys round-trip, and unknown
keys are rejected.

See docs/proactive_signals/claudedevs_intel_v2_design.md §13.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


# Sentinel for "use the bundled lanes.yaml shipped with the package."
DEFAULT_LANE_CONFIG_PACKAGE = "universal_agent.config"
DEFAULT_LANE_CONFIG_FILENAME = "intel_lanes.yaml"

CLAUDE_CODE_LANE_KEY = "claude-code-intelligence"


class LaneConfig(BaseModel):
    """One lane definition. Strict — unknown fields fail loudly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    title: str
    description: str = ""
    handles: list[str] = Field(default_factory=list)
    research_allowlist: list[str] = Field(default_factory=list)
    vault_slug: str
    capability_library_slug: str
    cron_expr: str
    cron_timezone: str = "America/Chicago"
    demo_endpoint_profile: str = "anthropic_native"
    tracked_packages: list[str] = Field(default_factory=list)

    @field_validator("handles", mode="before")
    @classmethod
    def _strip_handle_at_signs(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        return [str(item).strip().lstrip("@") for item in v if str(item).strip()]


class LanesDocument(BaseModel):
    """Top-level lanes.yaml schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = 1
    lanes: dict[str, LaneConfig]


def _load_yaml_text(path: Path | None) -> str:
    if path is not None:
        return Path(path).read_text(encoding="utf-8")
    # Use importlib.resources so the YAML works whether the package is
    # installed normally, in editable mode, or zipped.
    return (
        resources.files(DEFAULT_LANE_CONFIG_PACKAGE)
        .joinpath(DEFAULT_LANE_CONFIG_FILENAME)
        .read_text(encoding="utf-8")
    )


def load_lanes_document(path: Path | None = None) -> LanesDocument:
    """Parse and validate the lanes config. Pass `path` to override the bundled file."""
    raw = yaml.safe_load(_load_yaml_text(path)) or {}
    return LanesDocument.model_validate(raw)


@lru_cache(maxsize=1)
def _cached_default_document() -> LanesDocument:
    return load_lanes_document(None)


def get_lane(slug: str, *, path: Path | None = None) -> LaneConfig:
    """Return one lane by slug. Raises KeyError if missing."""
    doc = load_lanes_document(path) if path is not None else _cached_default_document()
    if slug not in doc.lanes:
        raise KeyError(f"intel lane not configured: {slug!r}")
    return doc.lanes[slug]


def enabled_lanes(*, path: Path | None = None) -> dict[str, LaneConfig]:
    """All lanes with `enabled: true`."""
    doc = load_lanes_document(path) if path is not None else _cached_default_document()
    return {slug: lane for slug, lane in doc.lanes.items() if lane.enabled}


def all_lanes(*, path: Path | None = None) -> dict[str, LaneConfig]:
    """All lanes regardless of enabled state."""
    doc = load_lanes_document(path) if path is not None else _cached_default_document()
    return dict(doc.lanes)


def reset_cache() -> None:
    """Clear the cached default document. Tests use this when monkey-patching env."""
    _cached_default_document.cache_clear()
