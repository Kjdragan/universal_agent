"""Phase 4 of the graded-judge redesign: the A/B harness self-agreement control
arm. Covers the pure ``_self_agreement`` reducer and that ``_render_markdown``
surfaces the noise-floor line so the operator reads batched agreement RELATIVE to
intrinsic variance (the live-prod arms themselves need ZAI creds + the prod DBs).
"""

from __future__ import annotations

from universal_agent.scripts import zai_batch_triage_ab as ab


def _specs(*ids):
    return [{"candidate_id": i, "thesis": f"t-{i}"} for i in ids]


def test_self_agreement_perfect():
    specs = _specs("a", "b", "c")
    run = {"a": "ship", "b": "skip", "c": "ship"}
    sa = ab._self_agreement(specs, run, dict(run))
    assert sa["agree"] == 3
    assert sa["disagree"] == 0
    assert sa["pct"] == 100.0
    assert sa["flips"] == []


def test_self_agreement_counts_flips():
    specs = _specs("a", "b", "c", "d")
    run_a = {"a": "ship", "b": "skip", "c": "ship", "d": "defer"}
    run_b = {"a": "ship", "b": "ship", "c": "ship", "d": "skip"}  # b, d flipped
    sa = ab._self_agreement(specs, run_a, run_b)
    assert sa["agree"] == 2
    assert sa["disagree"] == 2
    assert sa["pct"] == 50.0
    flipped = {f["id"] for f in sa["flips"]}
    assert flipped == {"b", "d"}


def test_self_agreement_empty():
    sa = ab._self_agreement([], {}, {})
    assert sa["pct"] == 0.0
    assert sa["agree"] == 0


def test_render_markdown_includes_noise_floor():
    report = {
        "generated_at": "2026-06-14T00:00:00Z",
        "results": [
            {
                "kind": "triage",
                "n": 4,
                "batch_size": 8,
                "per_item": {"calls": 4, "est_input_tokens": 100, "fup_or_429": 0, "latency_ms": 10.0},
                "batched": {"calls": 1, "est_input_tokens": 30, "fup_or_429": 0, "latency_ms": 5.0},
                "call_reduction": "4 → 1",
                "input_token_reduction_pct": 70.0,
                "agreement": {"agree": 2, "disagree": 2, "pct": 50.0},
                "self_agreement": {"agree": 4, "disagree": 0, "pct": 100.0, "flips": []},
                "divergences": [],
            }
        ],
    }
    md = ab._render_markdown(report)
    assert "Self-agreement (noise floor): 4/4 = 100.0%" in md
    assert "Verdict agreement: 2/4 = 50.0%" in md


def test_render_markdown_omits_noise_floor_when_absent():
    report = {
        "generated_at": "2026-06-14T00:00:00Z",
        "results": [
            {
                "kind": "triage",
                "n": 1,
                "batch_size": 8,
                "per_item": {"calls": 1, "est_input_tokens": 100, "fup_or_429": 0, "latency_ms": 10.0},
                "batched": {"calls": 1, "est_input_tokens": 30, "fup_or_429": 0, "latency_ms": 5.0},
                "call_reduction": "1 → 1",
                "input_token_reduction_pct": 70.0,
                "agreement": {"agree": 1, "disagree": 0, "pct": 100.0},
                "divergences": [],
            }
        ],
    }
    md = ab._render_markdown(report)
    assert "Self-agreement" not in md  # control arm not run ⇒ no noise-floor line
