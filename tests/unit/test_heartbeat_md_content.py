"""R4: guard against silent re-bloat of memory/HEARTBEAT.md and dangling
`memory/reference/*.md` pointers.

memory/HEARTBEAT.md is read whole by Simone via a `Read` tool call every
heartbeat tick (see DEFAULT_HEARTBEAT_PROMPT in heartbeat_service.py — it is
NOT statically injected into the system prompt). Keeping the core file small
keeps that per-tick tool-result cost down; the situation-specific protocol
detail lives in memory/reference/*.md, pointed to from core with
`FIRST Read memory/reference/<file>.md` bullets.
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
HEARTBEAT_PATH = REPO_ROOT / "memory" / "HEARTBEAT.md"

# R4 target: the pre-R4 file was 45,852 bytes. The restructure moves
# situation-specific (class b/c) content out to memory/reference/*.md,
# leaving only always/most-tick content in core. 20,000 bytes is the hard
# ceiling; a future edit that re-bloats the core past this should fail loudly
# rather than silently regress the context-diet savings.
MAX_CORE_BYTES = 20_000

REFERENCE_POINTER_RE = re.compile(r"memory/reference/[A-Za-z0-9._-]+\.md")


def test_heartbeat_core_stays_under_byte_budget():
    content = HEARTBEAT_PATH.read_bytes()
    assert len(content) <= MAX_CORE_BYTES, (
        f"memory/HEARTBEAT.md is {len(content)} bytes, over the {MAX_CORE_BYTES}-byte "
        "core budget (R4 context diet). Move new situation-specific content to "
        "memory/reference/<topic>.md with a pointer, rather than growing core."
    )


def test_heartbeat_core_every_reference_pointer_target_exists():
    content = HEARTBEAT_PATH.read_text(encoding="utf-8")
    pointers = sorted(set(REFERENCE_POINTER_RE.findall(content)))
    assert pointers, "expected at least one memory/reference/*.md pointer in HEARTBEAT.md"
    missing = [p for p in pointers if not (REPO_ROOT / p).is_file()]
    assert not missing, f"HEARTBEAT.md points at reference files that don't exist: {missing}"


def test_heartbeat_core_still_contains_class_a_anchors():
    """Sanity check that the restructure didn't accidentally delete always-fires
    content: Operating Intent, the routing matrix, and the two health checks
    that explicitly say 'run every heartbeat cycle' must remain in core."""
    content = HEARTBEAT_PATH.read_text(encoding="utf-8")
    for anchor in (
        "## Operating Intent",
        "### Default routing matrix",
        "VPS System Health Check (run every heartbeat cycle",
        "Local Desktop Health Check (run every heartbeat cycle)",
        "Proactive Activity Watchdog (run every heartbeat cycle)",
        "## Novelty Policy",
        "## Response Policy",
    ):
        assert anchor in content, f"expected class-a anchor {anchor!r} to remain in core HEARTBEAT.md"
