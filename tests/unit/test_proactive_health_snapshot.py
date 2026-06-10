"""Unit tests for the durable proactive_health snapshot store (S5 Phase C).

Pins the cross-process contract the deploy-independent timer and the heartbeat
share: a singleton "latest" row in activity_state.db, with the digest-cooldown
state preserved across runs so the 6h "don't re-spam" window survives a fresh
oneshot subprocess.
"""

from __future__ import annotations

import sqlite3

from universal_agent.services import proactive_health_snapshot as snap


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row  # mirror durable.db.connect_runtime_db
    return c


def _payload(*, criticals: int = 0, warns: int = 0, overall: str = "ok") -> dict:
    invariants: list[dict] = []
    for i in range(criticals):
        invariants.append(
            {
                "finding_id": f"crit_{i}",
                "metric_key": f"cm_{i}",
                "severity": "critical",
                "title": f"C{i}",
            }
        )
    for i in range(warns):
        invariants.append(
            {
                "finding_id": f"warn_{i}",
                "metric_key": f"wm_{i}",
                "severity": "warn",
                "title": f"W{i}",
            }
        )
    return {
        "overall_status": overall,
        "generated_at_utc": "2026-06-05T00:00:00+00:00",
        "invariants": invariants,
    }


def test_read_missing_table_returns_none():
    # A reader must tolerate the timer never having written (no DDL on read).
    assert snap.read_latest_snapshot(_conn()) is None


def test_write_then_read_roundtrip():
    c = _conn()
    snap.write_snapshot(
        c, payload=_payload(criticals=2, warns=1, overall="critical"),
        updated_at_utc="2026-06-05T01:00:00+00:00",
    )
    row = snap.read_latest_snapshot(c)
    assert row is not None
    assert row["overall_status"] == "critical"
    assert row["critical_count"] == 2
    assert row["warn_count"] == 1
    assert row["updated_at_utc"] == "2026-06-05T01:00:00+00:00"
    assert row["payload"]["invariants"][0]["finding_id"] == "crit_0"
    assert row["last_digest_fingerprint"] is None
    assert row["last_digest_sent_at_utc"] is None


def test_singleton_upsert_overwrites_not_appends():
    c = _conn()
    snap.write_snapshot(c, payload=_payload(criticals=1), updated_at_utc="t1")
    snap.write_snapshot(c, payload=_payload(criticals=3), updated_at_utc="t2")
    assert c.execute("SELECT COUNT(*) FROM proactive_health_snapshots").fetchone()[0] == 1
    row = snap.read_latest_snapshot(c)
    assert row["critical_count"] == 3
    assert row["updated_at_utc"] == "t2"


def test_digest_fields_preserved_via_coalesce_when_none():
    """The common 'didn't send a digest this run' path must NOT reset the
    cooldown — the 6h window keeps ticking from the original send."""
    c = _conn()
    snap.write_snapshot(
        c, payload=_payload(criticals=1), updated_at_utc="t1",
        digest_fingerprint="crit_0", digest_sent_at_utc="2026-06-05T01:00:00+00:00",
    )
    snap.write_snapshot(c, payload=_payload(criticals=1), updated_at_utc="t2")
    row = snap.read_latest_snapshot(c)
    assert row["last_digest_fingerprint"] == "crit_0"
    assert row["last_digest_sent_at_utc"] == "2026-06-05T01:00:00+00:00"
    assert row["updated_at_utc"] == "t2"  # the snapshot itself still advances


def test_digest_fields_overwritten_when_provided():
    c = _conn()
    snap.write_snapshot(
        c, payload=_payload(criticals=1), updated_at_utc="t1",
        digest_fingerprint="old", digest_sent_at_utc="t1",
    )
    snap.write_snapshot(
        c, payload=_payload(criticals=2), updated_at_utc="t2",
        digest_fingerprint="new", digest_sent_at_utc="t2",
    )
    row = snap.read_latest_snapshot(c)
    assert row["last_digest_fingerprint"] == "new"
    assert row["last_digest_sent_at_utc"] == "t2"


def test_count_by_severity():
    assert snap.count_by_severity(_payload(criticals=3, warns=2)) == (3, 2)
    assert snap.count_by_severity({"invariants": []}) == (0, 0)
    assert snap.count_by_severity({}) == (0, 0)


def test_compute_finding_fingerprint_helper_is_order_independent():
    # NB: compute_finding_fingerprint is RETAINED for introspection but is no
    # longer the digest dedup key (that exact-set equality was the bug — a
    # flapping invariant re-fired the digest on every membership change). The
    # dedup key is now decide_digest(); see the tests below. These assertions
    # only pin the helper's order-independent / metric_key-fallback behavior.
    a = [{"finding_id": "b"}, {"finding_id": "a"}]
    b = [{"finding_id": "a"}, {"finding_id": "b"}]
    assert snap.compute_finding_fingerprint(a) == snap.compute_finding_fingerprint(b)
    # falls back to metric_key when no finding_id
    assert snap.compute_finding_fingerprint([{"metric_key": "m"}]) == "m"
    assert snap.compute_finding_fingerprint([]) == ""


# ─── decide_digest: the dedup KEY (cumulative alerted-id set + sliding window) ──
# Replaces exact-set equality so a flapping invariant cannot re-fire the digest
# on every membership change, while a genuinely-new critical still pages.

_COOLDOWN = 21600.0  # 6h, the production default
_NOW = 1_000_000.0


def test_split_and_join_ids_roundtrip():
    assert snap._split_ids(None) == set()
    assert snap._split_ids("") == set()
    assert snap._split_ids("a|b|a") == {"a", "b"}
    assert snap._join_ids(["b", "a", "b"]) == "a|b"
    assert snap._join_ids([]) == ""


def _decide(current, prev_fp, last_sent_ts):
    return snap.decide_digest(
        current_finding_ids=current,
        prev_fingerprint=prev_fp,
        last_sent_ts=last_sent_ts,
        now_ts=_NOW,
        cooldown_seconds=_COOLDOWN,
    )


def test_decide_first_ever_critical_sends():
    # (a) no prior state → a brand-new critical pages immediately.
    assert _decide(["yt"], None, None) == (True, "yt")


def test_decide_same_set_within_window_suppressed():
    # (b) the same critical, still firing, within the window → silent.
    assert _decide(["yt"], "yt", _NOW - 600) == (False, "yt")


def test_decide_disappearance_within_window_suppressed():
    # (c) THE BUG: a critical leaving the set must NOT generate an email.
    # prev alerted {yt, zai}; zai dropped out → current {yt} ⊆ alerted → silent.
    assert _decide(["yt"], "yt|zai", _NOW - 600) == (False, "yt|zai")


def test_decide_new_id_within_window_sends_and_unions():
    # (d) a genuinely-new critical id within the window → page, carry the set.
    assert _decide(["yt", "zai"], "yt", _NOW - 600) == (True, "yt|zai")


def test_decide_reappearance_of_alerted_id_suppressed():
    # (f) an id already alerted this window flapping back in → silent.
    assert _decide(["yt", "zai"], "yt|zai", _NOW - 600) == (False, "yt|zai")


def test_decide_window_lapsed_renudges_once():
    # (e) still-firing critical after the window lapses → re-nudge (≤1 / window).
    assert _decide(["yt"], "yt", _NOW - _COOLDOWN - 10) == (True, "yt")


def test_decide_no_criticals_never_sends():
    # (g) empty set → no send regardless of prior state.
    assert _decide([], "yt", _NOW - 600) == (False, "")


def test_decide_lapsed_window_drops_stale_alerted_set():
    # After the window lapses, the carried-forward set resets: a NEW id no longer
    # unions with the stale alerted set, it starts a fresh window.
    assert _decide(["zai"], "yt", _NOW - _COOLDOWN - 10) == (True, "zai")


def test_read_tolerates_plain_tuple_rows():
    # A connection without row_factory yields tuples; read must still work.
    c = sqlite3.connect(":memory:")
    snap.write_snapshot(c, payload=_payload(criticals=1), updated_at_utc="t1")
    row = snap.read_latest_snapshot(c)
    assert row is not None
    assert row["critical_count"] == 1
    assert row["payload"]["overall_status"] == "ok"
