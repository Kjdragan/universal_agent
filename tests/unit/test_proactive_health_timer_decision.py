"""Regression lock for the proactive_health digest dedup DECISION.

The deploy-independent timer (``proactive_health_timer_main``) used to key its
6h cooldown on the EXACT set of critical finding-ids
(``compute_finding_fingerprint``) and suppress only when that set was identical.
A *flapping* invariant (e.g. ``zai_inference_health``'s rolling-10-min 429
burst) toggles set membership faster than the cooldown, so every add OR remove
looked like "a new/changed finding-set" and re-fired the digest — verified live
on the VPS on 2026-06-10 (a 2-critical email at 01:10 followed by a 1-critical
email at 01:20, 10 minutes apart, both inside the 6h window).

The dedup key is now ``proactive_health_snapshot.decide_digest`` — a cumulative
set of finding-ids already alerted within the active window. This test replays
that exact journal sequence through the same state-threading the timer does and
asserts the spurious 01:20 re-send no longer happens, while a genuinely-new
critical and a post-window re-nudge both still fire. The timer's decision branch
previously had NO test coverage, which is why the regression shipped.
"""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from universal_agent.services import proactive_health_snapshot as snap
from universal_agent.services.proactive_health_snapshot import (
    ACK_MAX_LIFETIME_DAYS,
)

_COOLDOWN = 21600.0  # 6h, the production default
_TICK = 600.0  # the timer fires every 10 min


def _run_sequence(steps, *, start_fp=None, start_sent=None, cooldown=_COOLDOWN):
    """Thread decide_digest through a tick sequence exactly as the timer does.

    On a real send the timer stamps the new alerted-set fingerprint and
    ``last_digest_sent_at_utc = now`` (sliding the window); on a suppress it
    writes None and the COALESCE upsert preserves the prior cooldown state.
    ``steps`` is a list of ``(now_ts, current_finding_ids)``. Returns a list of
    ``(sent, stored_fingerprint)`` after each step.
    """
    fp, sent_ts = start_fp, start_sent
    out = []
    for now_ts, current in steps:
        do_send, next_fp = snap.decide_digest(
            current_finding_ids=current,
            prev_fingerprint=fp,
            last_sent_ts=sent_ts,
            now_ts=now_ts,
            cooldown_seconds=cooldown,
        )
        if do_send:
            fp, sent_ts = next_fp, now_ts
        out.append((do_send, fp))
    return out


def test_journal_replay_flapping_zai_does_not_respam():
    # {yt} first alerted at t=0; ticks every 10 min thereafter. zai joins the
    # critical set at t=3000 (the 01:10 send) then drops at t=3600 (01:20).
    steps = [
        (1 * _TICK, ["yt"]),                 # 00:30  same set, in-window
        (2 * _TICK, ["yt"]),                 # 00:40
        (3 * _TICK, ["yt"]),                 # 00:50
        (4 * _TICK, ["yt"]),                 # 01:00
        (5 * _TICK, ["yt", "zai"]),          # 01:10  zai joins  -> SEND (new id)
        (6 * _TICK, ["yt"]),                 # 01:20  zai drops   -> was the bug
        (7 * _TICK, ["yt"]),                 # 01:30
        (8 * _TICK, ["yt"]),                 # 01:40
    ]
    out = _run_sequence(steps, start_fp="yt", start_sent=0.0)
    sent_flags = [sent for sent, _ in out]

    # Exactly ONE send across the window — the genuinely-new zai at 01:10.
    assert sum(sent_flags) == 1
    assert sent_flags == [False, False, False, False, True, False, False, False]
    # The 01:20 disappearance (index 5) is the regression: it MUST stay silent.
    assert out[5][0] is False
    # And the alerted set retains zai for the rest of the window (so a zai
    # re-appearance would also stay silent).
    assert out[5][1] == "yt|zai"


def test_new_distinct_critical_still_pages_next_tick():
    # A brand-new id appearing mid-window pages on the very next tick, never
    # waiting out the 6h window.
    steps = [
        (1 * _TICK, ["yt"]),          # in-window, already alerted -> silent
        (2 * _TICK, ["yt", "disk"]),  # brand-new "disk" -> SEND
    ]
    out = _run_sequence(steps, start_fp="yt", start_sent=0.0)
    assert [s for s, _ in out] == [False, True]
    assert out[1][1] == "disk|yt"


def test_persistent_critical_renudges_once_per_window():
    # The same single critical, firing continuously, re-nudges at most once per
    # cooldown: silent until the window lapses, then exactly one send.
    steps = [
        (1 * _TICK, ["yt"]),               # in-window -> silent
        (_COOLDOWN - 10, ["yt"]),          # still in-window -> silent
        (_COOLDOWN + 10, ["yt"]),          # window lapsed   -> SEND
        (_COOLDOWN + 10 + _TICK, ["yt"]),  # fresh window    -> silent
    ]
    out = _run_sequence(steps, start_fp="yt", start_sent=0.0)
    assert [s for s, _ in out] == [False, False, True, False]


# ─── Acknowledge filter (suppress-until-recovered with hysteresis) ────────────
# Replays the timer's per-tick ack pipeline exactly as `_run` threads it:
# reconcile_acks → get_active_acks → drop acked ids from the digest decision
# (`excluded_ids` keeps them out of the carried-forward alerted set too).

_RECOVERY = 21600  # the production default green-streak window


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _ack_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    snap.ensure_ack_schema(conn)
    return conn


def _run_acked_sequence(conn, steps, *, start_fp=None, start_sent=None,
                        recovery=_RECOVERY, cooldown=_COOLDOWN):
    """Mirror of _run_sequence with the timer's ack reconcile+filter step."""
    fp, sent_ts = start_fp, start_sent
    out = []
    for now_ts, current in steps:
        snap.reconcile_acks(
            conn,
            current_critical_ids=current,
            now_iso=_iso(now_ts),
            recovery_seconds=recovery,
        )
        acked = set(snap.get_active_acks(conn).keys())
        unacked = [i for i in current if i not in acked]
        do_send, next_fp = snap.decide_digest(
            current_finding_ids=unacked,
            prev_fingerprint=fp,
            last_sent_ts=sent_ts,
            now_ts=now_ts,
            cooldown_seconds=cooldown,
            excluded_ids=acked,
        )
        if do_send:
            fp, sent_ts = next_fp, now_ts
        out.append((do_send, unacked))
    return out


def test_acked_critical_is_muted_even_across_window_lapses():
    # Operator got the t=0 email for yt and clicked Acknowledge. yt keeps
    # firing red far past the 6h cooldown — without the ack the lapsed window
    # would re-nudge; with it, every tick stays silent.
    conn = _ack_conn()
    snap.record_ack(conn, finding_id="yt", ack_source="email_link", now_iso=_iso(300))
    steps = [
        (1 * _TICK, ["yt"]),
        (_COOLDOWN + 10, ["yt"]),      # would re-nudge if unacked
        (2 * _COOLDOWN + 10, ["yt"]),  # ditto
    ]
    out = _run_acked_sequence(conn, steps, start_fp="yt", start_sent=0.0)
    assert [s for s, _ in out] == [False, False, False]
    # And the ack is still active (last_red touched every red tick).
    assert "yt" in snap.get_active_acks(conn)


def test_ack_does_not_mute_other_new_criticals():
    # Muting yt must not swallow a genuinely-new id: disk pages immediately.
    conn = _ack_conn()
    snap.record_ack(conn, finding_id="yt", ack_source="email_link", now_iso=_iso(300))
    steps = [
        (1 * _TICK, ["yt"]),
        (2 * _TICK, ["yt", "disk"]),  # new unacked id -> SEND (without yt)
    ]
    out = _run_acked_sequence(conn, steps, start_fp="yt", start_sent=0.0)
    assert [s for s, _ in out] == [False, True]
    assert out[1][1] == ["disk"]  # the digest covers only the unacked finding


def test_ack_hysteresis_brief_green_dip_does_not_recover():
    # Criticals flap on a minutes scale: a green dip SHORTER than the recovery
    # window must keep the ack active (no email on the re-red).
    conn = _ack_conn()
    snap.record_ack(conn, finding_id="yt", ack_source="email_link", now_iso=_iso(300))
    steps = [
        (1 * _TICK, ["yt"]),   # red — touches last_red
        (2 * _TICK, []),       # green dip (10 min << 6h recovery)
        (3 * _TICK, ["yt"]),   # re-red — still muted, NOT a new finding
        (4 * _TICK, ["yt"]),
    ]
    out = _run_acked_sequence(conn, steps, start_fp="yt", start_sent=0.0)
    assert [s for s, _ in out] == [False, False, False, False]
    assert "yt" in snap.get_active_acks(conn)


def test_acked_id_refires_after_recovery_then_re_red():
    # Full lifecycle: red+acked -> green long enough -> ack recovers -> a NEW
    # red alerts again immediately (the acked id never lingers in the
    # alerted-set fingerprint).
    conn = _ack_conn()
    snap.record_ack(conn, finding_id="yt", ack_source="email_link", now_iso=_iso(300))
    last_red = 1 * _TICK
    recovered_at = last_red + _RECOVERY + _TICK  # green streak > recovery window
    steps = [
        (last_red, ["yt"]),             # red while acked -> silent
        (last_red + _TICK, []),         # goes green
        (recovered_at, []),             # green long enough -> ack recovers
        (recovered_at + _TICK, ["yt"]),  # NEW red -> SEND immediately
    ]
    out = _run_acked_sequence(conn, steps, start_fp="yt", start_sent=0.0)
    assert [s for s, _ in out] == [False, False, False, True]
    assert snap.get_active_acks(conn) == {}  # ack retired (recovered)


def test_recovery_refire_not_blocked_by_live_cooldown_window():
    # The pre-ack send stamped yt into the fingerprint, and a LATER send (for
    # zai) keeps the window live. yt's post-recovery re-red inside that window
    # must still page — excluded_ids strips acked ids from the carried set.
    conn = _ack_conn()
    snap.record_ack(conn, finding_id="yt", ack_source="email_link", now_iso=_iso(300))
    last_red = 1 * _TICK
    recovered_at = last_red + _RECOVERY + _TICK
    steps = [
        (last_red, ["yt"]),                    # acked -> silent
        (last_red + _TICK, ["zai"]),           # new id -> SEND (slides window)
        (recovered_at, ["zai"]),               # yt green-streak completes
        (recovered_at + _TICK, ["yt", "zai"]),  # yt re-red inside zai's window -> SEND
    ]
    out = _run_acked_sequence(
        conn, steps, start_fp="yt", start_sent=0.0, cooldown=10 * _COOLDOWN
    )
    assert [s for s, _ in out] == [False, True, False, True]


def test_ack_max_lifetime_backstop_force_recovers_a_still_red_finding():
    # An ack can never outlive ACK_MAX_LIFETIME_DAYS, even while the finding
    # is still red — the next tick after expiry re-arms alerting.
    conn = _ack_conn()
    snap.record_ack(conn, finding_id="yt", ack_source="email_link", now_iso=_iso(0))
    expiry = ACK_MAX_LIFETIME_DAYS * 86400
    steps = [
        (expiry - _TICK, ["yt"]),  # one tick before the backstop -> muted
        (expiry + _TICK, ["yt"]),  # past it -> ack force-recovered -> SEND
    ]
    out = _run_acked_sequence(conn, steps, start_fp=None, start_sent=None)
    assert [s for s, _ in out] == [False, True]
    assert snap.get_active_acks(conn) == {}


def test_record_ack_is_idempotent_and_audit_preserving():
    conn = _ack_conn()
    first = snap.record_ack(conn, finding_id="yt", ack_source="email_link", now_iso=_iso(0))
    again = snap.record_ack(conn, finding_id="yt", ack_source="email_link", now_iso=_iso(0))
    assert first["created"] is True
    assert again["created"] is False
    assert again["id"] == first["id"]
    # Recovery flips status (row is kept, never deleted)…
    snap.reconcile_acks(
        conn, current_critical_ids=[], now_iso=_iso(10**9), recovery_seconds=60
    )
    rows = conn.execute("SELECT status FROM proactive_health_acks").fetchall()
    assert [r["status"] for r in rows] == ["recovered"]
    # …and a fresh ack afterwards starts a NEW row (audit trail accumulates).
    fresh = snap.record_ack(conn, finding_id="yt", ack_source="email_link")
    assert fresh["created"] is True
    assert fresh["id"] != first["id"]
    assert (
        conn.execute("SELECT COUNT(*) FROM proactive_health_acks").fetchone()[0] == 2
    )
