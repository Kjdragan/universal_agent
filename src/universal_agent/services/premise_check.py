"""Deterministic verify-against-reality helpers (top-9 handoff, task 8).

Kills the "claimed done ≠ actually done" class at its cheapest point: a
claim that names a concrete code artifact (a route, a cron producer, a
systemd timer, a cited symbol) is mechanically checkable with a grep — no
LLM judgment required. Two real incidents seeded this module: completion
narratives cited a `/api/v1/hackernews/refresh` "producer" (no such route
exists — the path only ever appears in prose) and the retired
`csi_analytics` trend-timer "producer" (no cron registration, no systemd
unit).

`verify_code_claim` understands structured claims:

    route:/api/v1/foo          — an HTTP route with this path is registered
    cron:some_system_job       — a system cron registration exists
    timer:unit-name            — a deployment/systemd unit file exists
    symbol:pkg/mod.py::name    — the cited file defines the symbol
    <anything else>            — plain literal grep over src/

`verify_mission_against_reality` is the per-mission-type completion gate:
falsifiable checks for the types that define one (tutorial_build /
directed_build: the landed demo workspace + manifest.json actually exist on
disk, independently re-checked — never trusting a self-report), and an
explicit fail-OPEN `no_check_defined` verdict for every other type (never
block work it can't reason about).
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

VERIFIED = "Verified"
UNVERIFIED = "Unverified"


@dataclass(frozen=True)
class ClaimVerdict:
    status: str  # VERIFIED | UNVERIFIED
    claim: str
    kind: str
    evidence: str

    @property
    def verified(self) -> bool:
        return self.status == VERIFIED


def _repo_root() -> Path:
    """<repo>/src/universal_agent/services/premise_check.py -> <repo>."""
    return Path(__file__).resolve().parents[3]


def _iter_src_files(repo_root: Path) -> Iterator[Path]:
    src = repo_root / "src" / "universal_agent"
    if not src.is_dir():
        return
    own = Path(__file__).resolve()
    for path in src.rglob("*.py"):
        if path.resolve() == own:
            continue  # this module's own docstrings must not vouch for claims
        yield path


def _grep_src(repo_root: Path, needle: str) -> Optional[str]:
    """Return 'path:lineno' of the first src line containing ``needle``."""
    for path in _iter_src_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle not in text:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if needle in line:
                try:
                    rel = path.relative_to(repo_root)
                except ValueError:
                    rel = path
                return f"{rel}:{lineno}"
    return None


def _check_route(repo_root: Path, route_path: str) -> Optional[str]:
    """A route exists iff its path appears as a call argument — `("<path>"` —
    the decorator/registration syntax. Prose mentions in docstrings (exactly
    how the hackernews false premise survived) do not match."""
    return _grep_src(repo_root, f'("{route_path}"')


def _check_cron(repo_root: Path, key: str) -> Optional[str]:
    # Direct registration literal, the constant-assignment pattern many
    # registrations use (PAPER_TO_PODCAST_JOB_KEY = "paper_to_podcast_daily"
    # then system_job=PAPER_TO_PODCAST_JOB_KEY), or a systemd unit. A bare
    # quoted mention deliberately does NOT count — `"csi_analytics": 0` is a
    # counters dict key, not a producer registration.
    for needle in (
        f'system_job="{key}"',
        f"system_job='{key}'",
        f'= "{key}"',
        f"= '{key}'",
    ):
        hit = _grep_src(repo_root, needle)
        if hit:
            return hit
    return _check_timer(repo_root, key)


def _check_timer(repo_root: Path, name: str) -> Optional[str]:
    for unit_dir in ("deployment/systemd", "infrastructure/systemd"):
        d = repo_root / unit_dir
        if not d.is_dir():
            continue
        for unit in sorted(d.iterdir()):
            if name in unit.name:
                return str(unit.relative_to(repo_root))
    return None


def _check_symbol(repo_root: Path, ref: str) -> Optional[str]:
    """``pkg/mod.py::name`` — the file exists and defines the symbol."""
    file_part, _, symbol = ref.partition("::")
    symbol = symbol.strip()
    file_part = file_part.strip()
    if not file_part or not symbol:
        return None
    candidates = [repo_root / file_part, repo_root / "src" / file_part]
    if not file_part.startswith("src/") and "/" not in file_part:
        candidates.append(repo_root / "src" / "universal_agent" / file_part)
    target = next((c for c in candidates if c.is_file()), None)
    if target is None:
        # Fall back: match by basename anywhere under src.
        base = os.path.basename(file_part)
        target = next(
            (p for p in _iter_src_files(repo_root) if p.name == base), None
        )
    if target is None:
        return None
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    pattern = re.compile(
        rf"^\s*(?:async\s+def|def|class)\s+{re.escape(symbol)}\b|^{re.escape(symbol)}\s*[:=]",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        return None
    lineno = text[: m.start()].count("\n") + 1
    try:
        rel = target.relative_to(repo_root)
    except ValueError:
        rel = target
    return f"{rel}:{lineno}"


def verify_code_claim(claim: str, *, repo_root: Optional[Path] = None) -> ClaimVerdict:
    """Mechanically verify a code-artifact claim. Never raises.

    Returns a :class:`ClaimVerdict` with ``status`` VERIFIED (evidence names
    the file:line / unit file) or UNVERIFIED (evidence says what was
    searched). Unknown-shaped claims degrade to a plain literal grep.
    """
    root = repo_root or _repo_root()
    text = str(claim or "").strip()
    if not text:
        return ClaimVerdict(UNVERIFIED, claim="", kind="empty", evidence="empty claim")
    kind, _, arg = text.partition(":")
    arg = arg.strip()
    try:
        if kind == "route" and arg:
            hit = _check_route(root, arg)
            return ClaimVerdict(
                VERIFIED if hit else UNVERIFIED,
                claim=text,
                kind="route",
                evidence=hit or f"no route registration for {arg!r} in src/ "
                "(prose mentions do not count)",
            )
        if kind == "cron" and arg:
            hit = _check_cron(root, arg)
            return ClaimVerdict(
                VERIFIED if hit else UNVERIFIED,
                claim=text,
                kind="cron",
                evidence=hit or f"no system_job registration or systemd unit for {arg!r}",
            )
        if kind == "timer" and arg:
            hit = _check_timer(root, arg)
            return ClaimVerdict(
                VERIFIED if hit else UNVERIFIED,
                claim=text,
                kind="timer",
                evidence=hit or f"no systemd unit matching {arg!r}",
            )
        if kind == "symbol" and arg:
            hit = _check_symbol(root, arg)
            return ClaimVerdict(
                VERIFIED if hit else UNVERIFIED,
                claim=text,
                kind="symbol",
                evidence=hit or f"cited symbol {arg!r} does not resolve",
            )
        hit = _grep_src(root, text)
        return ClaimVerdict(
            VERIFIED if hit else UNVERIFIED,
            claim=text,
            kind="literal",
            evidence=hit or "literal not found in src/",
        )
    except Exception as exc:  # noqa: BLE001 — a checker crash must not block work
        logger.debug("verify_code_claim failed for %r: %s", claim, exc)
        return ClaimVerdict(UNVERIFIED, claim=text, kind=kind or "unknown", evidence=f"checker error: {exc}")


# ── Per-mission-type completion gate ────────────────────────────────────────

NO_CHECK_DEFINED = "no_check_defined"


def verify_mission_against_reality(
    *,
    source_kind: str,
    finalize_result: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Falsifiable completion check for one mission type at a time.

    tutorial_build / directed_build (the piloted type): the landed demo
    workspace and its ``manifest.json`` must exist ON DISK — re-checked here
    with plain os calls, independent of the finalize code's self-report.

    Every other type: fail-OPEN with an explicit ``no_check_defined``
    verdict (surfaced as a warn-level stamp, never a block) — the gate must
    not reason about work it has no check for. Never raises.
    """
    kind = str(source_kind or "").strip()
    try:
        if kind in {"tutorial_build", "directed_build"}:
            workspace = str((finalize_result or {}).get("workspace_dir") or "").strip()
            if not workspace:
                return {
                    "status": UNVERIFIED,
                    "source_kind": kind,
                    "check": "demo_workspace_manifest_exists",
                    "evidence": "finalize reported no workspace path",
                }
            ws = Path(workspace).expanduser()
            manifest = ws / "manifest.json"
            if ws.is_dir() and manifest.is_file():
                return {
                    "status": VERIFIED,
                    "source_kind": kind,
                    "check": "demo_workspace_manifest_exists",
                    "evidence": str(manifest),
                }
            return {
                "status": UNVERIFIED,
                "source_kind": kind,
                "check": "demo_workspace_manifest_exists",
                "evidence": (
                    f"workspace_dir exists={ws.is_dir()} manifest exists="
                    f"{manifest.is_file()} at {ws}"
                ),
            }
        return {
            "status": NO_CHECK_DEFINED,
            "source_kind": kind,
            "check": None,
            "evidence": "fail-open: no falsifiable check defined for this mission type",
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("verify_mission_against_reality failed: %s", exc)
        return {
            "status": NO_CHECK_DEFINED,
            "source_kind": kind,
            "check": None,
            "evidence": f"fail-open: checker error: {exc}",
        }
