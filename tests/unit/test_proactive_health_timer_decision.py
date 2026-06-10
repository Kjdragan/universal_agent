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

from universal_agent.services import proactive_health_snapshot as snap

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
