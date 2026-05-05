"""Auto-trigger the dependency upgrade actuator from release_announcement actions.

PR 6a wired the classifier to detect `action_type=release_announcement` and
attach a structured `release_info` payload (package, version, is_anthropic_adjacent)
to the action. PR 6b built the actuator that bumps pyproject.toml and runs
both smokes. PR 6c is the wiring that closes the loop: when the run_report
script finishes a poll tick that contains release announcements for
Anthropic-adjacent packages, fire the actuator for each.

Per the v2 design (§5):
  - Auto-upgrade is ON by default for Anthropic-adjacent packages
  - Smoke-test gated (PR 6b enforces)
  - Rollback on failure (PR 6b enforces)
  - Email Kevin on every change (PR 6b sends; this module aggregates per tick)

Off switch: UA_CSI_AUTO_UPGRADE_ON_RELEASE=0 (default 1/on).

Idempotency:
  - If two ticks both detect the same `(package, version)` release, the
    second call to apply_upgrade is a no-op because bump_pyproject_dep
    short-circuits when current_version == target_version.
  - Cross-ticks state is the live pyproject.toml itself.

See docs/proactive_signals/claudedevs_intel_v2_design.md §5.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Any, Iterable

from universal_agent.services.dependency_currency import is_anthropic_adjacent
from universal_agent.services.dependency_upgrade import (
    UpgradeOutcome,
    apply_upgrade,
)

logger = logging.getLogger(__name__)


def auto_upgrade_enabled() -> bool:
    """Off switch — env UA_CSI_AUTO_UPGRADE_ON_RELEASE. Default ON in v2."""
    raw = str(os.getenv("UA_CSI_AUTO_UPGRADE_ON_RELEASE") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


# ── Release extraction ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReleaseTrigger:
    """One release announcement that should fire the actuator."""

    package: str
    version: str
    post_id: str
    handle: str = ""
    post_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "version": self.version,
            "post_id": self.post_id,
            "handle": self.handle,
            "post_url": self.post_url,
        }


def extract_release_triggers(
    actions: Iterable[dict[str, Any]],
    *,
    handle: str = "",
    only_anthropic_adjacent: bool = True,
) -> list[ReleaseTrigger]:
    """Pull the release_announcement actions out of an actions stream.

    Filters to Anthropic-adjacent packages by default (PR 6c's whole point
    is auto-upgrading the gating packages; non-adjacent releases are
    interesting but not auto-applied without explicit operator approval).
    """
    out: list[ReleaseTrigger] = []
    seen: set[tuple[str, str]] = set()  # dedupe (package, version) within one tick
    for action in actions:
        if str(action.get("action_type") or "") != "release_announcement":
            continue
        info = action.get("release_info") if isinstance(action.get("release_info"), dict) else None
        if not info:
            continue
        package = str(info.get("package") or "").strip()
        version = str(info.get("version") or "").strip()
        if not (package and version):
            continue
        if only_anthropic_adjacent and not (
            bool(info.get("is_anthropic_adjacent"))
            or is_anthropic_adjacent(package)
        ):
            continue
        key = (package, version)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ReleaseTrigger(
                package=package,
                version=version,
                post_id=str(action.get("post_id") or ""),
                handle=handle,
                post_url=str(action.get("url") or ""),
            )
        )
    return out


# ── Actuator orchestration ──────────────────────────────────────────────────


@dataclass(frozen=True)
class AutoUpgradeResult:
    """Per-trigger outcome record."""

    trigger: ReleaseTrigger
    skipped_reason: str = ""
    outcome: UpgradeOutcome | None = None

    @property
    def attempted(self) -> bool:
        return self.outcome is not None

    @property
    def overall_ok(self) -> bool:
        return bool(self.outcome and self.outcome.overall_ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger.to_dict(),
            "attempted": self.attempted,
            "skipped_reason": self.skipped_reason,
            "overall_ok": self.overall_ok,
            "outcome": self.outcome.to_dict() if self.outcome else None,
        }


def auto_apply_release_triggers(
    triggers: Iterable[ReleaseTrigger],
    *,
    repo_root: Path | None = None,
    smoke_dir: Path | None = None,
    backup_dir: Path | None = None,
    enabled: bool | None = None,
) -> list[AutoUpgradeResult]:
    """For each trigger, invoke apply_upgrade. Returns per-trigger results.

    `enabled=None` reads the env var. Pass an explicit bool to override
    (used by tests).
    """
    is_enabled = auto_upgrade_enabled() if enabled is None else bool(enabled)
    results: list[AutoUpgradeResult] = []
    for trigger in triggers:
        if not is_enabled:
            results.append(
                AutoUpgradeResult(
                    trigger=trigger,
                    skipped_reason="auto_upgrade_disabled_via_env",
                )
            )
            continue
        try:
            outcome = apply_upgrade(
                package=trigger.package,
                target_version=trigger.version,
                repo_root=repo_root,
                smoke_dir=smoke_dir,
                backup_dir=backup_dir,
            )
            results.append(AutoUpgradeResult(trigger=trigger, outcome=outcome))
        except FileNotFoundError as exc:
            results.append(
                AutoUpgradeResult(
                    trigger=trigger,
                    skipped_reason=f"pyproject_missing: {exc}",
                )
            )
        except KeyError as exc:
            # Package not declared in pyproject.toml — that's a config drift
            # but not a programmer error from this module's perspective.
            # Surface it as a skip; operator can review.
            results.append(
                AutoUpgradeResult(
                    trigger=trigger,
                    skipped_reason=f"package_not_in_pyproject: {exc}",
                )
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("auto-upgrade unexpectedly raised for %s", trigger.package)
            results.append(
                AutoUpgradeResult(
                    trigger=trigger,
                    skipped_reason=f"unexpected_error: {type(exc).__name__}: {exc}",
                )
            )
    return results


# ── Summary helpers (consumed by the run_report email path) ─────────────────


def summarize_auto_upgrade_results(results: list[AutoUpgradeResult]) -> dict[str, Any]:
    """Compact rollup for inclusion in the operator email."""
    return {
        "trigger_count": len(results),
        "attempted_count": sum(1 for r in results if r.attempted),
        "succeeded_count": sum(1 for r in results if r.overall_ok),
        "skipped": [r.skipped_reason for r in results if not r.attempted],
        "results": [r.to_dict() for r in results],
    }
