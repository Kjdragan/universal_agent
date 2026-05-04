"""Mission Control Intelligence System — backend sweeper daemon.

Phase 0 scaffolding. Defines the `MissionControlSweeper` class that, in
later phases, will drive the three-tier intelligence pipeline:

  - Tier 0: poll the 9 traffic-light tiles every tick
  - Tier 1: gate LLM card-discovery passes by bundle-signature change
  - Tier 2: gate page-synthesis on tier-0 transitions or tier-1 success
  - Drain at most 1 in-flight glm-4.7 call at a time on the dedicated lane

Phase 0 ships the skeleton ONLY. The sweeper does not run anywhere yet,
and `tick()` is a no-op until `UA_MC_PHASE_1_ENABLED=1` flips on tier-0
behavior. This file exists so:

  - the import surface is stable across phases
  - tests can verify the feature-flag gating without spinning real work
  - downstream phases can fill in the tier handlers in-place

See docs/02_Subsystems/Mission_Control_Intelligence_System.md §2.2 and §9.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from typing import Any, Callable

from universal_agent.services.mission_control_cards import (
    SEVERITY_CRITICAL,
    SEVERITY_INFORMATIONAL,
    SEVERITY_WARNING,
    SEVERITY_WATCHING,
    SUBJECT_INFRASTRUCTURE,
    CardUpsert,
    live_card_exists_for_subject,
    make_card_id,
    retire_card,
    upsert_card,
)
from universal_agent.services.mission_control_db import is_phase_enabled, open_store
from universal_agent.services.mission_control_tiles import (
    COLOR_GREEN,
    COLOR_RED,
    COLOR_UNKNOWN,
    COLOR_YELLOW,
    Tile,
    TileState,
    all_tiles,
)

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _get_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return default


@dataclass
class SweeperConfig:
    """Runtime configuration for the Mission Control sweeper.

    All fields are env-overridable. Defaults match the policy table in
    docs/02_Subsystems/Mission_Control_Intelligence_System.md §2.3.
    """

    interval_seconds: float = 60.0
    tier1_floor_seconds: float = 180.0
    tier1_ceiling_seconds: float = 1800.0
    tier2_floor_seconds: float = 120.0
    tier2_ceiling_seconds: float = 300.0
    lane_concurrency: int = 1
    auto_remediation_enabled: bool = False

    @classmethod
    def from_env(cls) -> "SweeperConfig":
        return cls(
            interval_seconds=_get_float_env("UA_MISSION_CONTROL_SWEEPER_INTERVAL_S", 60.0),
            tier1_floor_seconds=_get_float_env("UA_MISSION_CONTROL_TIER1_FLOOR_S", 180.0),
            tier1_ceiling_seconds=_get_float_env("UA_MISSION_CONTROL_TIER1_CEILING_S", 1800.0),
            tier2_floor_seconds=_get_float_env("UA_MISSION_CONTROL_TIER2_FLOOR_S", 120.0),
            tier2_ceiling_seconds=_get_float_env("UA_MISSION_CONTROL_TIER2_CEILING_S", 300.0),
            lane_concurrency=max(1, _get_int_env("UA_MISSION_CONTROL_LANE_CONCURRENCY", 1)),
            auto_remediation_enabled=(os.getenv("UA_MISSION_CONTROL_AUTO_REMEDIATION") or "0").strip().lower()
                in {"1", "true", "yes", "on", "enabled"},
        )


@dataclass
class SweepResult:
    """Outcome of a single sweeper tick. Used for observability + tests."""

    started_at_utc: str
    finished_at_utc: str
    tier0_checked: bool = False
    tier0_transitions: list[str] = field(default_factory=list)
    tier1_evaluated: bool = False
    tier1_synthesized: bool = False
    tier2_evaluated: bool = False
    tier2_synthesized: bool = False
    skipped_reason: str | None = None
    errors: list[str] = field(default_factory=list)


class MissionControlSweeper:
    """Single-process backend sweeper.

    Phase 0: structural skeleton only. `tick()` early-returns with a
    `skipped_reason` until later phases enable their respective tiers.

    The sweeper is intentionally NOT a long-running async task at this
    phase. Wiring the gateway to call `tick()` on a schedule is part of
    Phase 1's deliverable.
    """

    def __init__(self, config: SweeperConfig | None = None) -> None:
        self.config = config or SweeperConfig.from_env()

    # ── Public API ─────────────────────────────────────────────────────

    def tick(self) -> SweepResult:
        """Run a single sweep cycle.

        Returns a `SweepResult` describing what was checked, what was
        skipped, and any errors. Always safe to call — never raises.
        """
        started = _utc_now_iso()

        if not is_phase_enabled(1):
            return SweepResult(
                started_at_utc=started,
                finished_at_utc=_utc_now_iso(),
                skipped_reason="phase_1_not_enabled",
            )

        # Phase 1+ implementation lands here. Each tier handler will be
        # filled in incrementally without changing this orchestration
        # surface.
        result = SweepResult(started_at_utc=started, finished_at_utc=started)
        try:
            self._run_tier0(result)
            if is_phase_enabled(2):
                self._run_tier1(result)
            if is_phase_enabled(3):
                self._run_tier2(result)
        except Exception as exc:  # never crash the sweeper loop
            logger.exception("Mission Control sweeper tick failed")
            result.errors.append(str(exc))
        finally:
            result.finished_at_utc = _utc_now_iso()
        return result

    # ── Tier handlers ──────────────────────────────────────────────────

    def _run_tier0(self, result: SweepResult) -> None:
        """Tier-0 sweep: poll tiles, persist state, detect transitions,
        auto-create infrastructure cards on yellow/red transitions.

        Errors inside an individual tile are caught and recorded in
        `result.errors` so a buggy tile cannot take out the whole tier-0
        cycle. State and transition writes happen inside a single MC
        store connection per sweep, so concurrent reads from the gateway
        always see a consistent snapshot.
        """
        result.tier0_checked = True
        try:
            mc_conn = open_store()
        except Exception as exc:  # store init failure is fatal for tier-0
            result.errors.append(f"open_store failed: {exc}")
            return

        try:
            data_conn = self._open_activity_db()
        except Exception as exc:
            result.errors.append(f"activity DB open failed: {exc}")
            mc_conn.close()
            return

        try:
            for tile in all_tiles():
                try:
                    state = tile.compute_state(data_conn)
                except Exception as exc:
                    logger.exception("tile %s compute_state failed", tile.name)
                    result.errors.append(f"tile {tile.name}: {exc}")
                    continue

                outcome = self._persist_tile_state(mc_conn, tile, state)
                if outcome.get("transitioned"):
                    result.tier0_transitions.append(
                        f"{tile.name}:{outcome['prior_color']}->{state.color}"
                    )

                # Invariant: a non-green tile MUST have a corresponding live
                # infrastructure card. Conditions for creating/refreshing the
                # card on this sweep:
                #   - color is yellow/red, AND
                #   - either (a) no live card exists for this subject (covers
                #     fresh boot, prior buggy run, manually-retired card), or
                #     (b) the tile transitioned to a non-green color (to
                #     refresh narrative + push prior into history).
                # Identical-color sweeps with an existing live card do nothing
                # to avoid bloating synthesis_history with duplicate entries.
                if state.color in {COLOR_YELLOW, COLOR_RED}:
                    has_live_card = live_card_exists_for_subject(
                        mc_conn, SUBJECT_INFRASTRUCTURE, f"infra:{tile.name}"
                    )
                    is_color_transition = (
                        outcome.get("transitioned")
                        and outcome.get("prior_color") != state.color
                    )
                    if not has_live_card or is_color_transition:
                        if outcome.get("first_appearance"):
                            trigger = "first_appearance"
                        elif not has_live_card:
                            trigger = "missing_card_backfill"
                        else:
                            trigger = "transition"
                        try:
                            self._auto_create_infrastructure_card(
                                mc_conn, tile, state, trigger=trigger,
                            )
                        except Exception as exc:
                            logger.exception("auto-card creation failed for %s", tile.name)
                            result.errors.append(f"auto-card {tile.name}: {exc}")
                # Mirror invariant: a GREEN tile MUST NOT have a stale live
                # infra card from a prior yellow/red state. Without this
                # retirement step, infra cards become immortal — production
                # bug 2026-05-04 had a CSI Ingester "Silent 48+ Hours" card
                # surviving long after the tile flipped back to green,
                # which kept poisoning the Chief-of-Staff brief synthesis.
                # Tier-1 explicitly skips infrastructure cards on retirement
                # ("owned by tier-0"), so tier-0 has to do this itself.
                elif state.color == COLOR_GREEN:
                    if live_card_exists_for_subject(
                        mc_conn, SUBJECT_INFRASTRUCTURE, f"infra:{tile.name}"
                    ):
                        try:
                            retire_card(
                                mc_conn,
                                make_card_id(
                                    SUBJECT_INFRASTRUCTURE, f"infra:{tile.name}"
                                ),
                            )
                        except Exception as exc:
                            logger.exception(
                                "infra card retire failed for %s", tile.name
                            )
                            result.errors.append(
                                f"retire-on-green {tile.name}: {exc}"
                            )
        finally:
            data_conn.close()
            mc_conn.close()

    def _run_tier1(self, result: SweepResult) -> None:
        """Sync marker — sets `tier1_evaluated=True` so the gating
        contract is observable in `tick()` results. The actual LLM work
        happens in `_run_tier1_async` which the gateway loop awaits
        AFTER calling `tick()`. We split sync/async to avoid forcing
        every existing tick() caller (incl. tests) to deal with
        coroutines.
        """
        result.tier1_evaluated = True

    def _run_tier2(self, result: SweepResult) -> None:
        """Sync marker; real tier-2 synthesis lands in
        `_run_tier2_async` (Phase 3)."""
        result.tier2_evaluated = True

    # ── Async tiers (LLM-driven; gateway loop awaits these) ────────────

    async def run_async_tiers(self, result: SweepResult) -> None:
        """Run tier-1 + tier-2 LLM passes. Called by `run_sweeper_loop`
        AFTER the sync `tick()` returns. Gated by phase flags AND by
        bundle-signature/cadence so we don't burn glm-4.7/opus calls
        when nothing operationally meaningful has moved.

        Cascade contract (Phase 3.5):
          - tier-1 fires first; on success, tier-2 sees the new cards
            on the SAME sweep tick. No 30-min lag.
          - tier-2 fires when tier-0 transitioned this sweep, OR tier-1
            produced new cards this sweep, OR ceiling exceeded since
            last tier-2 run. Never more often than the floor.
        """
        if is_phase_enabled(2):
            try:
                await self._run_tier1_async(result)
            except Exception as exc:
                logger.exception("Tier-1 async pass failed")
                result.errors.append(f"tier1: {exc}")
        # Tier-2 runs unconditionally (no separate phase flag) because
        # the existing Chief-of-Staff service has been live since before
        # Phase 0; we're just adding sweeper-driven cadence on top.
        try:
            await self._run_tier2_async(result)
        except Exception as exc:
            logger.exception("Tier-2 async pass failed")
            result.errors.append(f"tier2: {exc}")

    async def force_refresh_async(
        self,
        *,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Operator-driven full refresh: run tier-1 (cards) then tier-2
        (Chief-of-Staff readout) NOW, bypassing the cadence gating
        windows the natural sweeper loop honors.

        Phases reported via the optional `progress` callback:
          - "cards_running" — tier-1 LLM card-discovery has started
          - "readout_running" — tier-1 finished; tier-2 synthesis started
          - "completed" — tier-2 finished successfully
          - "failed" — either phase raised; payload carries
            `phase ∈ {"cards","readout"}` and a stringified `error`

        Never raises. Returns a summary dict the gateway worker stores
        in the in-memory job tracker so the dashboard can render it.

        Tier-1 failure short-circuits — tier-2 is NOT run, because the
        readout would synthesize from stale cards and re-stale the
        operator-visible brief (the bug we are fixing).
        """
        from datetime import datetime, timezone

        def _now_iso() -> str:
            return datetime.now(timezone.utc).isoformat()

        def _emit(_phase: str, **payload: Any) -> None:
            if progress is None:
                return
            try:
                progress(_phase, {"at": _now_iso(), **payload})
            except Exception:
                # A buggy callback must never break the refresh — just log.
                logger.exception("force_refresh progress callback raised")

        result = SweepResult(started_at_utc=_now_iso(), finished_at_utc=_now_iso())
        summary: dict[str, Any] = {
            "status": "running",
            "tier0_checked": False,
            "tier1_synthesized": False,
            "tier2_synthesized": False,
            "started_at": result.started_at_utc,
        }

        # Phase 0: tier-0 — recompute tile colors AND retire infra cards
        # for tiles that have flipped back to green. Without this, tier-1
        # would discover stale infra cards from a prior yellow/red state
        # and tier-2 would synthesize a brief that contradicts the live
        # tile strip. Production bug 2026-05-04. Failures here are
        # non-fatal: we still try tier-1/tier-2 so the operator sees as
        # much fresh state as possible.
        try:
            self._run_tier0(result)
            summary["tier0_checked"] = True
        except Exception as exc:
            logger.exception("force_refresh: tier-0 raised")
            # Surface as a soft failure — keep going to tier-1/tier-2.
            result.errors.append(f"force_refresh_tier0: {exc}")

        # Phase 1: tier-1 (cards)
        _emit("cards_running")
        try:
            await self._run_tier1_async(result, force=True)
        except Exception as exc:
            logger.exception("force_refresh: tier-1 raised")
            err = f"{type(exc).__name__}: {exc}"
            summary.update(
                status="failed",
                failed_phase="cards",
                error=err,
                finished_at=_now_iso(),
            )
            _emit("failed", phase="cards", error=err)
            return summary
        summary["tier1_synthesized"] = bool(result.tier1_synthesized)

        # Phase 2: tier-2 (readout)
        _emit("readout_running")
        try:
            await self._run_tier2_async(result, force=True)
        except Exception as exc:
            logger.exception("force_refresh: tier-2 raised")
            err = f"{type(exc).__name__}: {exc}"
            summary.update(
                status="failed",
                failed_phase="readout",
                error=err,
                finished_at=_now_iso(),
            )
            _emit("failed", phase="readout", error=err)
            return summary
        summary["tier2_synthesized"] = bool(result.tier2_synthesized)

        summary.update(status="completed", finished_at=_now_iso())
        # Surface the latest readout id (if any) so the UI can deep-link.
        try:
            from universal_agent.services.mission_control_chief_of_staff import (
                get_latest_readout,
            )
            latest = get_latest_readout()
            if isinstance(latest, dict):
                summary["readout_id"] = latest.get("id")
        except Exception:
            logger.debug("force_refresh: latest readout lookup failed", exc_info=True)
        _emit("completed", readout_id=summary.get("readout_id"))
        return summary

    async def _run_tier1_async(self, result: SweepResult, *, force: bool = False) -> None:
        """Tier-1 LLM card-discovery pass.

        Always-writes a `__tier1_meta__` row at the end (success, skip,
        OR exception path) so the diagnostics endpoint can show exactly
        what happened on the last attempt. Without this, a silent
        exception leaves operators staring at `tier1_meta: null` with
        no way to diagnose.

        When `force=True` the floor/ceiling cadence gate is bypassed.
        Used only by the operator-driven `force_refresh_async` path —
        the natural sweeper loop always passes `force=False`.
        """
        from universal_agent.services.mission_control_db import open_store
        from universal_agent.services.mission_control_tier1 import (
            apply_tier1_discovery,
            collect_tier1_evidence,
            discover_tier1_cards,
            evidence_signature,
        )

        last_attempt_summary = "tier1 not attempted (init failure)"
        last_evidence_payload: dict[str, Any] | None = None
        last_signature: str | None = None

        try:
            mc_conn = open_store()
        except Exception as exc:
            result.errors.append(f"tier1 open_store failed: {exc}")
            return  # can't write meta row without a connection

        try:
            try:
                data_conn = self._open_activity_db()
            except Exception as exc:
                last_attempt_summary = f"tier1 init failed: activity DB open: {exc}"
                result.errors.append(f"tier1 activity DB open failed: {exc}")
                self._write_tier1_meta(mc_conn, signature=None, annotation=last_attempt_summary, payload=None)
                return

            try:
                evidence = collect_tier1_evidence(data_conn, mc_conn)
            except Exception as exc:
                last_attempt_summary = f"tier1 init failed: evidence collection: {exc}"
                result.errors.append(f"tier1 evidence collection failed: {exc}")
                data_conn.close()
                self._write_tier1_meta(mc_conn, signature=None, annotation=last_attempt_summary, payload=None)
                return
            finally:
                try:
                    data_conn.close()
                except Exception:
                    pass

            sig = evidence_signature(evidence)
            last_signature = sig
            last_evidence_payload = {
                "evidence_counts": evidence.get("counts"),
            }

            meta_row = mc_conn.execute(
                "SELECT current_state, last_signature, state_since FROM mission_control_tile_states "
                "WHERE tile_id = ?",
                ("__tier1_meta__",),
            ).fetchone()
            prior_sig = meta_row["last_signature"] if meta_row else None
            # state_since is the only ts column we set per write; treat it
            # as last-synthesis time for the floor/ceiling check
            prior_synth_iso = meta_row["state_since"] if meta_row else None
            ceiling_seconds = self.config.tier1_ceiling_seconds
            floor_seconds = self.config.tier1_floor_seconds

            skip_reason = (
                None
                if force
                else self._tier1_skip_reason(
                    prior_sig, sig, prior_synth_iso, floor_seconds, ceiling_seconds
                )
            )
            if skip_reason:
                last_attempt_summary = f"tier1 skipped: {skip_reason}"
                self._write_tier1_meta(mc_conn, signature=sig, annotation=last_attempt_summary,
                                       payload=last_evidence_payload)
                logger.debug("Tier-1 skipped: %s", skip_reason)
                return

            try:
                upserts, model_used = await discover_tier1_cards(evidence)
            except Exception as exc:
                last_attempt_summary = f"tier1 LLM discover_tier1_cards raised: {type(exc).__name__}: {exc}"
                result.errors.append(last_attempt_summary)
                logger.exception("Tier-1 discover raised")
                self._write_tier1_meta(mc_conn, signature=sig, annotation=last_attempt_summary,
                                       payload=last_evidence_payload)
                return

            try:
                summary = apply_tier1_discovery(mc_conn, upserts)
            except Exception as exc:
                last_attempt_summary = f"tier1 apply_tier1_discovery raised: {type(exc).__name__}: {exc}"
                result.errors.append(last_attempt_summary)
                logger.exception("Tier-1 apply raised")
                self._write_tier1_meta(mc_conn, signature=sig, annotation=last_attempt_summary,
                                       payload={"model": model_used, "upsert_count": len(upserts), **(last_evidence_payload or {})})
                return

            result.tier1_synthesized = True
            last_attempt_summary = (
                f"tier1 ok: created/updated={len(summary['created_or_updated'])} "
                f"retired={len(summary['retired'])} errors={len(summary['errors'])} model={model_used}"
            )
            full_payload = {
                "model": model_used,
                "summary": summary,
                "evidence_counts": evidence.get("counts"),
            }
            self._write_tier1_meta(mc_conn, signature=sig, annotation=last_attempt_summary, payload=full_payload)
            logger.info("🛰️  %s", last_attempt_summary)
        finally:
            try:
                mc_conn.close()
            except Exception:
                pass

    @staticmethod
    def _write_tier1_meta(
        conn,
        *,
        signature: str | None,
        annotation: str,
        payload: dict[str, Any] | None,
    ) -> None:
        """Always-callable meta-row writer for tier-1 attempt outcomes.

        Records to mission_control_tile_states under tile_id='__tier1_meta__'
        so the diagnostics endpoint can surface the last attempt's status,
        signature, and payload. Catches its own exceptions so a meta-write
        failure cannot mask the real tier-1 outcome.
        """
        now_iso = _utc_now_iso()
        try:
            conn.execute(
                """
                INSERT INTO mission_control_tile_states (
                    tile_id, current_state, state_since, last_signature,
                    last_checked_at, last_annotation_at,
                    current_annotation, evidence_payload_json
                ) VALUES (?, 'unknown', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tile_id) DO UPDATE SET
                    state_since = excluded.state_since,
                    last_signature = excluded.last_signature,
                    last_checked_at = excluded.last_checked_at,
                    last_annotation_at = excluded.last_annotation_at,
                    current_annotation = excluded.current_annotation,
                    evidence_payload_json = excluded.evidence_payload_json
                """,
                (
                    "__tier1_meta__",
                    now_iso,
                    signature or "",
                    now_iso,
                    now_iso,
                    annotation,
                    json.dumps(payload, default=str) if payload is not None else None,
                ),
            )
        except Exception:
            logger.exception("Tier-1 meta-row write failed")

    @staticmethod
    def _tier1_skip_reason(
        prior_sig: str | None,
        new_sig: str,
        prior_synth_iso: str | None,
        floor_seconds: float,
        ceiling_seconds: float,
    ) -> str | None:
        """Decide whether to skip this tier-1 pass.

        Skip when:
          - signature unchanged AND last call within ceiling window
          - signature changed BUT last call within floor window
            (rate-limit; defer to next sweep)
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        last_synth = None
        if prior_synth_iso:
            try:
                last_synth = datetime.fromisoformat(prior_synth_iso.replace("Z", "+00:00"))
                if last_synth.tzinfo is None:
                    last_synth = last_synth.replace(tzinfo=timezone.utc)
            except ValueError:
                last_synth = None
        age_s = (now - last_synth).total_seconds() if last_synth else float("inf")

        if prior_sig == new_sig and age_s < ceiling_seconds:
            return f"signature_unchanged (age={int(age_s)}s, ceiling={int(ceiling_seconds)}s)"
        if prior_sig != new_sig and age_s < floor_seconds:
            return f"signature_changed_but_rate_limited (age={int(age_s)}s, floor={int(floor_seconds)}s)"
        return None

    # ── Tier-2 (Chief-of-Staff) cascade — Phase 3.5 ────────────────────

    async def _run_tier2_async(self, result: SweepResult, *, force: bool = False) -> None:
        """Tier-2 sweep: refresh the Chief-of-Staff readout when state has
        meaningfully moved.

        Cascade triggers (any one fires it):
          - tier-0 transitioned this sweep (color flip on a tile)
          - tier-1 produced new cards this sweep (`tier1_synthesized`)
          - last tier-2 run > ceiling_seconds ago (idle-system refresh)

        Floor: never more than once every floor_seconds. Even if a
        cascade trigger fires, we honor the floor to protect the LLM
        lane from bursting.

        When `force=True` the cascade + floor/ceiling gate is bypassed.
        Used only by the operator-driven `force_refresh_async` path.

        Always-writes a `__tier2_meta__` row so the diagnostics
        endpoint can show last attempt status — same pattern as tier-1.
        """
        from universal_agent.services.mission_control_db import open_store

        try:
            mc_conn = open_store()
        except Exception as exc:
            result.errors.append(f"tier2 open_store failed: {exc}")
            return

        try:
            # Read prior tier-2 attempt timestamp
            meta_row = mc_conn.execute(
                "SELECT state_since FROM mission_control_tile_states WHERE tile_id = ?",
                ("__tier2_meta__",),
            ).fetchone()
            prior_synth_iso = meta_row["state_since"] if meta_row else None

            cascade_reason = self._tier2_cascade_reason(result)
            if force:
                cascade_reason = cascade_reason or "operator_force_refresh"
                skip_reason = None
            else:
                skip_reason = self._tier2_skip_reason(
                    cascade_reason=cascade_reason,
                    prior_synth_iso=prior_synth_iso,
                    floor_seconds=self.config.tier2_floor_seconds,
                    ceiling_seconds=self.config.tier2_ceiling_seconds,
                )
            if skip_reason:
                self._write_tier2_meta(
                    mc_conn,
                    annotation=f"tier2 skipped: {skip_reason}",
                    payload={"cascade_reason": cascade_reason},
                )
                logger.debug("Tier-2 skipped: %s", skip_reason)
                return

            # Run the existing Chief-of-Staff service. It already does:
            # collect_evidence_bundle() (which Phase 3 made card-aware),
            # synthesize_readout() against the configured COS model,
            # persist_readout() to the durable store. We just drive it
            # from the sweeper instead of an HTTP click.
            try:
                from universal_agent.services.mission_control_chief_of_staff import (
                    generate_and_store_readout,
                )
            except Exception as exc:
                self._write_tier2_meta(
                    mc_conn,
                    annotation=f"tier2 init failed: COS import: {exc}",
                    payload={"cascade_reason": cascade_reason},
                )
                result.errors.append(f"tier2 import failed: {exc}")
                return

            try:
                readout = await generate_and_store_readout()
            except Exception as exc:
                annotation = f"tier2 COS raised: {type(exc).__name__}: {exc}"
                logger.exception("Tier-2 COS raised")
                result.errors.append(annotation)
                self._write_tier2_meta(
                    mc_conn,
                    annotation=annotation,
                    payload={"cascade_reason": cascade_reason},
                )
                return

            result.tier2_synthesized = True
            headline = ""
            model_used = None
            if isinstance(readout, dict):
                headline = str(readout.get("headline") or "")[:160]
                model_used = readout.get("model")
            self._write_tier2_meta(
                mc_conn,
                annotation=(
                    f"tier2 ok: cascade={cascade_reason} model={model_used} "
                    f"headline={headline!r}"
                )[:600],
                payload={
                    "cascade_reason": cascade_reason,
                    "model": model_used,
                    "headline": headline,
                },
            )
            logger.info(
                "🛰️  Tier-2 readout refreshed (cascade=%s, model=%s)",
                cascade_reason, model_used,
            )
        finally:
            try:
                mc_conn.close()
            except Exception:
                pass

    @staticmethod
    def _tier2_cascade_reason(result: SweepResult) -> str:
        """Identify which cascade signal (if any) should drive a tier-2
        refresh THIS sweep. Returns a human-readable reason string.

        Order of precedence:
          1. Tier-0 color transitions (always interesting)
          2. Tier-1 synthesis success (new cards)
          3. Empty string = no cascade trigger; tier-2 should only run
             on the ceiling fallback.
        """
        if result.tier0_transitions:
            n = len(result.tier0_transitions)
            return f"tier0_transitions:{n}"
        if result.tier1_synthesized:
            return "tier1_synthesized"
        return ""  # no cascade — only ceiling-driven refresh would fire

    @staticmethod
    def _tier2_skip_reason(
        cascade_reason: str,
        prior_synth_iso: str | None,
        floor_seconds: float,
        ceiling_seconds: float,
    ) -> str | None:
        """Decide whether to skip this tier-2 pass.

        Logic:
          - First run ever (no prior_synth_iso): always run.
          - Within floor: skip even on cascade — protect lane from
            bursting.
          - Past ceiling: always run (idle-system refresh keeps
            readout from staling).
          - Otherwise: run iff cascade_reason is non-empty.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        last_synth = None
        if prior_synth_iso:
            try:
                last_synth = datetime.fromisoformat(
                    prior_synth_iso.replace("Z", "+00:00")
                )
                if last_synth.tzinfo is None:
                    last_synth = last_synth.replace(tzinfo=timezone.utc)
            except ValueError:
                last_synth = None
        age_s = (now - last_synth).total_seconds() if last_synth else float("inf")

        # First-run: always go
        if last_synth is None:
            return None

        # Floor: never more than once per floor_seconds
        if age_s < floor_seconds:
            return (
                f"floor_protection (age={int(age_s)}s, "
                f"floor={int(floor_seconds)}s)"
            )

        # Ceiling: always refresh past it, regardless of cascade
        if age_s >= ceiling_seconds:
            return None

        # In the floor..ceiling window: only run on cascade
        if not cascade_reason:
            return (
                f"no_cascade_signal (age={int(age_s)}s, "
                f"waiting_for_transition_or_tier1_success)"
            )
        return None

    @staticmethod
    def _write_tier2_meta(
        conn,
        *,
        annotation: str,
        payload: dict[str, Any] | None,
    ) -> None:
        """Always-callable meta-row writer for tier-2 attempt outcomes.

        Mirrors `_write_tier1_meta` but for the `__tier2_meta__`
        sentinel row. Catches its own exceptions so a meta-write
        failure can never mask the real tier-2 outcome.
        """
        now_iso = _utc_now_iso()
        try:
            conn.execute(
                """
                INSERT INTO mission_control_tile_states (
                    tile_id, current_state, state_since,
                    last_signature, last_checked_at, last_annotation_at,
                    current_annotation, evidence_payload_json
                ) VALUES (?, 'unknown', ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tile_id) DO UPDATE SET
                    state_since = excluded.state_since,
                    last_checked_at = excluded.last_checked_at,
                    last_annotation_at = excluded.last_annotation_at,
                    current_annotation = excluded.current_annotation,
                    evidence_payload_json = excluded.evidence_payload_json
                """,
                (
                    "__tier2_meta__",
                    now_iso,
                    "",
                    now_iso,
                    now_iso,
                    annotation,
                    json.dumps(payload, default=str) if payload is not None else None,
                ),
            )
        except Exception:
            logger.exception("Tier-2 meta-row write failed")

    # ── Tier-0 helpers ─────────────────────────────────────────────────

    def _open_activity_db(self):  # type: ignore[no-untyped-def]
        """Open a connection to the activity / task_hub DB.

        Indirection so tests can override with a fixture-backed
        connection. Default implementation uses the standard runtime DB
        helper.
        """
        from universal_agent.durable.db import (
            connect_runtime_db,
            get_activity_db_path,
        )

        return connect_runtime_db(get_activity_db_path())

    def _persist_tile_state(
        self, conn, tile: Tile, state: TileState
    ) -> dict[str, Any]:
        """Write the new tile state. Returns an outcome dict describing
        what just happened so the caller can decide whether to fire
        downstream effects (card creation, transition logging).

        Outcome keys:
          - signature_unchanged: bool — true when no real state change
            since the last poll; only `last_checked_at` was bumped.
          - first_appearance: bool — true when this tile had no prior
            row at all (sweeper's first encounter with the tile, or DB
            wipe). Critical: a freshly-booted gateway with a red tile
            on its first tick must still emit a card, so this is the
            signal to do that.
          - transitioned: bool — true when the color actually changed.
          - prior_color: previous color, or None on first_appearance.
        """
        existing = conn.execute(
            """
            SELECT current_state, last_signature, state_since
            FROM mission_control_tile_states
            WHERE tile_id = ?
            """,
            (tile.name,),
        ).fetchone()
        now = _utc_now_iso()
        prior_color = existing["current_state"] if existing else None
        prior_signature = existing["last_signature"] if existing else None
        prior_since = existing["state_since"] if existing else None

        # If signature unchanged, just bump last_checked_at and bail.
        if existing is not None and prior_signature == state.signature:
            conn.execute(
                """
                UPDATE mission_control_tile_states
                SET last_checked_at = ?
                WHERE tile_id = ?
                """,
                (now, tile.name),
            )
            return {
                "signature_unchanged": True,
                "first_appearance": False,
                "transitioned": False,
                "prior_color": prior_color,
            }

        first_appearance = existing is None
        transitioned = prior_color is not None and prior_color != state.color
        state_since = now if (prior_color != state.color) else (prior_since or now)
        evidence_json = self._dump_json(state.evidence)

        if existing is None:
            conn.execute(
                """
                INSERT INTO mission_control_tile_states (
                    tile_id, current_state, state_since, last_signature,
                    last_checked_at, current_annotation, evidence_payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tile.name, state.color, state_since, state.signature, now,
                 state.one_line_status, evidence_json),
            )
        else:
            # current_annotation is the operator-facing one-line status.
            # Phase 1 writes the mechanical status; Phase 5 auto-diagnostic
            # LLM passes will overwrite with richer prose. We do NOT
            # overwrite current_annotation if a richer annotation was
            # written by a Phase 5+ pass since the last poll — preserving
            # LLM enrichment across mechanical re-polls.
            existing_annotation = existing["current_annotation"] if "current_annotation" in existing.keys() else None
            annotation_to_write = state.one_line_status
            if existing_annotation and existing["last_annotation_at"] and existing["current_state"] == state.color:
                annotation_to_write = existing_annotation
            conn.execute(
                """
                UPDATE mission_control_tile_states
                SET current_state = ?,
                    state_since = ?,
                    last_signature = ?,
                    last_checked_at = ?,
                    current_annotation = ?,
                    evidence_payload_json = ?
                WHERE tile_id = ?
                """,
                (state.color, state_since, state.signature, now,
                 annotation_to_write, evidence_json, tile.name),
            )
        return {
            "signature_unchanged": False,
            "first_appearance": first_appearance,
            "transitioned": transitioned,
            "prior_color": prior_color,
        }

    def _auto_create_infrastructure_card(
        self, conn, tile: Tile, state: TileState, *, trigger: str = "transition"
    ) -> None:
        """Create or revive the infrastructure-kind tier-1 card paired
        with this tile. Phase 1 uses the tile's mechanical status as
        narrative; Phase 5's diagnostic mission and Phase 2's tier-1
        discovery pass enrich it later.

        `trigger` is one of:
          - "transition"        — tile changed color (was different, now non-green)
          - "first_appearance"  — tile had no prior row and opens at non-green
        Phrased differently in narrative so the card audit trail is
        accurate after the fact.
        """
        severity = self._severity_for_color(state.color)
        if trigger == "first_appearance":
            opener = (
                f"Tile `{tile.name}` ({tile.display_name}) first observed at "
                f"`{state.color}` on this sweeper boot."
            )
        elif trigger == "missing_card_backfill":
            opener = (
                f"Tile `{tile.name}` ({tile.display_name}) is `{state.color}` and "
                "had no corresponding live card. Backfilling now to satisfy the "
                "non-green-tile-implies-live-card invariant."
            )
        else:
            opener = (
                f"Tile `{tile.name}` ({tile.display_name}) transitioned to "
                f"`{state.color}`."
            )
        narrative = (
            f"{opener} Mechanical status:\n  {state.one_line_status}\n\n"
            "Phase 1 produced this card mechanically from the tile state. "
            "Phase 2 tier-1 LLM discovery and Phase 5 auto-diagnostic missions "
            "will enrich the narrative on subsequent sweeps."
        )
        why_it_matters = (
            "Infrastructure tile transitions are the operator's first signal that "
            "a subsystem changed state. The auto-created card guarantees a deep-dive "
            "exists wherever a tile alarms — even before LLM enrichment runs."
        )
        upsert_card(
            conn,
            CardUpsert(
                subject_kind=SUBJECT_INFRASTRUCTURE,
                subject_id=f"infra:{tile.name}",
                severity=severity,
                title=f"{tile.display_name}: {state.one_line_status}",
                narrative=narrative,
                why_it_matters=why_it_matters,
                tags=["infrastructure", tile.name, state.color],
                evidence_refs=[
                    {
                        "kind": "tile",
                        "id": tile.name,
                        "uri": f"/dashboard/mission-control#tile-{tile.name}",
                        "label": f"{tile.display_name} tile",
                    }
                ],
                evidence_payload=state.evidence,
                synthesis_model=None,  # not LLM-synthesized in Phase 1
                evidence_signature=state.signature,
            ),
        )

    @staticmethod
    def _severity_for_color(color: str) -> str:
        if color == COLOR_RED:
            return SEVERITY_CRITICAL
        if color == COLOR_YELLOW:
            return SEVERITY_WARNING
        if color == COLOR_UNKNOWN:
            return SEVERITY_WATCHING
        if color == COLOR_GREEN:
            return SEVERITY_INFORMATIONAL
        return SEVERITY_INFORMATIONAL

    @staticmethod
    def _dump_json(value: Any) -> str | None:
        if value is None:
            return None
        try:
            return json.dumps(value, default=str)
        except (TypeError, ValueError):
            return json.dumps({"unserializable": str(value)})


# Module-level singleton so the gateway can grab a stable handle. The
# instance is cheap to construct; consumers should treat this as the
# canonical sweeper for the process.
_sweeper_instance: MissionControlSweeper | None = None


def get_sweeper() -> MissionControlSweeper:
    """Return the process-wide MissionControlSweeper singleton."""
    global _sweeper_instance
    if _sweeper_instance is None:
        _sweeper_instance = MissionControlSweeper()
    return _sweeper_instance


def reset_sweeper_for_tests() -> None:
    """Test-only helper: drop the singleton so a fresh config is picked up."""
    global _sweeper_instance
    _sweeper_instance = None


def _config_summary(cfg: SweeperConfig) -> dict[str, Any]:
    """Compact dict for logging / health endpoints."""
    return {
        "interval_seconds": cfg.interval_seconds,
        "tier1_floor_seconds": cfg.tier1_floor_seconds,
        "tier1_ceiling_seconds": cfg.tier1_ceiling_seconds,
        "tier2_floor_seconds": cfg.tier2_floor_seconds,
        "tier2_ceiling_seconds": cfg.tier2_ceiling_seconds,
        "lane_concurrency": cfg.lane_concurrency,
        "auto_remediation_enabled": cfg.auto_remediation_enabled,
    }


# ── Async loop wrapper for gateway lifespan ────────────────────────────


async def run_sweeper_loop(stop_event: "Any") -> None:
    """Background-task body for the gateway's `lifespan` startup hook.

    Awaits `stop_event.wait()` with a timeout equal to the configured
    sweeper interval. On every interval expiry it calls `tick()` once.
    Exits cleanly when `stop_event` is set so graceful shutdown works.

    The loop is itself defensive: any exception inside `tick()` (which
    should be rare since `tick()` already contains its handlers) is
    caught and logged so the loop keeps running.
    """
    import asyncio

    sweeper = get_sweeper()
    # Floor at 0.01s rather than 1.0s so unit tests can drive the loop
    # at sub-second cadence; production always sets a real interval via
    # UA_MISSION_CONTROL_SWEEPER_INTERVAL_S (default 60s).
    interval = max(0.01, sweeper.config.interval_seconds)
    logger.info(
        "🛰️  Mission Control sweeper loop starting (interval=%.1fs, model_lane=glm-4.7)",
        interval,
    )
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        try:
            result = sweeper.tick()
            if result.skipped_reason is None:
                # Now run the LLM-driven async tiers (tier-1, tier-2).
                # tick() handled tier-0 synchronously; the async pass
                # uses the dedicated glm-4.7 lane and is bundle-signature
                # gated to keep cost minimal.
                try:
                    await sweeper.run_async_tiers(result)
                except Exception:
                    logger.exception("Mission Control async tiers raised — loop continues")
            if result.skipped_reason is None and (
                result.tier0_transitions or result.errors or result.tier1_synthesized
            ):
                logger.info(
                    "🛰️  MC sweep: transitions=%s tier1=%s errors=%s",
                    result.tier0_transitions,
                    result.tier1_synthesized,
                    result.errors,
                )
        except Exception:
            logger.exception("Mission Control sweeper tick raised — loop continues")
    logger.info("🛰️  Mission Control sweeper loop stopped")
