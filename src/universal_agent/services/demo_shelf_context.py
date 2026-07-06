"""Capability-shelf context for the proactive-demo candidate judges (eureka bias).

Builds a bounded, human-readable summary of demo_factory's EXISTING skill
library — skill name + reuse count + one-line description — so the proactive-demo
candidate judges (buildability + end-of-day golden-nuggets) can prefer LANDMARK
candidates that EXTEND the shelf or fill obvious white space over incremental
me-toos that merely duplicate a capability family already on the shelf.

Design invariants (both load-bearing for a PRODUCTION judge):

1. **Fail-safe by construction.** ANY failure — the demo_factory checkout is
   absent on this host, a ``SKILL.md`` frontmatter is unreadable, ``_reuse.json``
   is missing/garbled — returns ``""``. The judges append the block
   unconditionally (``system += capability_shelf_block()``), so an empty shelf
   leaves their prompt BYTE-IDENTICAL to before this module existed.

2. **A prompt HINT only.** The shelf never changes a judge's score scale or its
   pass/reject threshold. It is a tie-breaker / bias the block's own wording
   makes explicit.

The demo_factory checkout is resolved the SAME way the engine override block
resolves it — ``feature_flags.proactive_demo_factory_script`` → repo root — so
the shelf reads the same checkout the build actually runs from.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bounds — keep the injected block small so it can never blow the judge's context.
_MAX_SKILLS = 60
_MAX_DESC_CHARS = 160

# Per-process cache keyed by resolved demo_factory root (the shelf changes only as
# skills accrue; re-reading disk on every candidate would be wasteful).
_CACHE: dict[str, str] = {}


def _resolve_demo_factory_root() -> Path:
    """The demo_factory repo root on the runtime host, resolved identically to the
    engine override block (``proactive_demo_factory_script`` → ``.../demo_factory``)."""
    from universal_agent.feature_flags import (
        _demo_factory_project_dir,
        proactive_demo_factory_script,
    )

    return Path(_demo_factory_project_dir(proactive_demo_factory_script()))


def _one_line(text: str, *, limit: int = _MAX_DESC_CHARS) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def _parse_frontmatter(text: str) -> dict:
    """Parse a ``SKILL.md`` YAML frontmatter block. Returns ``{}`` on anything odd."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        import yaml

        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}
    return meta if isinstance(meta, dict) else {}


def _load_reuse_counts(skills_dir: Path) -> dict[str, int]:
    """Read ``skills/_reuse.json`` (``{skill_name: count}``). ``{}`` on any failure."""
    try:
        data = json.loads((skills_dir / "_reuse.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, int] = {}
    for name, count in data.items():
        try:
            out[str(name)] = int(count)
        except (TypeError, ValueError):
            continue
    return out


def _build_shelf(root: Path) -> str:
    """Render the shelf listing (one line per skill), or ``""`` when there are no
    readable skills under ``<root>/skills``."""
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        return ""
    reuse = _load_reuse_counts(skills_dir)

    rows: list[tuple[int, str, str]] = []  # (reuse_count, name, one-line description)
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        dir_name = skill_md.parent.name
        try:
            meta = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        except Exception:
            continue
        name = str(meta.get("name") or dir_name).strip() or dir_name
        desc = _one_line(meta.get("description") or "")
        count = int(reuse.get(name, reuse.get(dir_name, 0)))
        rows.append((count, name, desc))

    if not rows:
        return ""

    # Highest-reuse first (so "extend a high-reuse skill" candidates are salient to
    # the judge), then alphabetical; cap the count.
    rows.sort(key=lambda r: (-r[0], r[1]))
    rows = rows[:_MAX_SKILLS]

    lines = [
        f"- {name} (reuse {count}): {desc}" if desc else f"- {name} (reuse {count})"
        for count, name, desc in rows
    ]
    return "\n".join(lines)


def build_capability_shelf(root: Optional[Path] = None) -> str:
    """Return the bounded shelf listing (one line per skill), or ``""`` on ANY
    failure. Per-process cached by resolved root. ``root`` is an injection seam for
    tests; production resolves it from ``feature_flags``."""
    try:
        resolved = Path(root) if root is not None else _resolve_demo_factory_root()
        key = str(resolved)
        if key in _CACHE:
            return _CACHE[key]
        shelf = _build_shelf(resolved)
        _CACHE[key] = shelf
        return shelf
    except Exception:
        logger.debug("capability shelf unavailable; judges run without it", exc_info=True)
        return ""


def capability_shelf_block(root: Optional[Path] = None) -> str:
    """The full injectable block (eureka instruction + shelf listing), or ``""``
    when the shelf is empty/unavailable — so a caller can do ``system +=
    capability_shelf_block()`` and stay byte-identical when there is no shelf."""
    shelf = build_capability_shelf(root)
    if not shelf:
        return ""
    return (
        "\n\n── FACTORY CAPABILITY SHELF (eureka bias) ──\n"
        "Below is the factory's EXISTING capability shelf — one line per skill as "
        "`name (reuse <count>): description`. Use it to bias candidate selection "
        "toward LANDMARK work, WITHOUT changing your score scale or threshold:\n"
        "- PREFER a landmark/eureka candidate (impressive on its own AND radiating "
        "several future directions) over an incremental me-too.\n"
        "- DOWN-RANK a candidate that merely duplicates a capability family already "
        "on the shelf.\n"
        "- UP-RANK a candidate that would EXTEND a high-reuse skill or fill obvious "
        "white space the shelf does not cover yet.\n"
        "This is a tie-breaker / bias only: judge each candidate on its own merits "
        "first, and keep the exact scoring rules above.\n\n"
        f"{shelf}\n"
        "── end capability shelf ──"
    )
