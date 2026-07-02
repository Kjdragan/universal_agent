"""Tier resolution for the ProcessTurnAdapter (``_tier_for_model``).

As of the 2026-06-14 liveness redesign, ``_tier_for_model`` no longer drives a
wall-clock kill — the adapter is governed by the idle / no-progress
``LivenessWatchdog`` (see ``test_liveness_watchdog.py`` /
``test_execution_engine_turn_timeout.py``). The mapping is now an **informational
label** in the adapter's liveness log line. It is retained (and still tested
here) because the glm-5.x→opus rule is the migration-proofing that prevents a
recurrence of the Simone daemon stall: after the glm-5.1→5.2 opus-map migration
(2026-06-13) a daemon session pinned to ``glm-5.1`` no longer matched
``ZAI_MODEL_MAP['opus'] == 'glm-5.2'`` and would mis-label as sonnet — back when
that label *did* drive a 180s kill (~13h with zero completed daemon turns). The
label is correct now so the historical journal lines stay meaningful, but the
kill it once caused is gone.
"""

import pytest

from universal_agent.execution_engine import _tier_for_model


@pytest.mark.parametrize("model,tier", [
    ("glm-5.1", "opus"),        # the migration-gap case that stalled Simone
    ("glm-5.2", "opus"),        # current opus flagship
    ("glm-5.3", "opus"),        # a future opus flagship — migration-proof
    ("glm-5-turbo", "sonnet"),  # DASH = sonnet, must NOT be read as opus
    ("glm-4.5-air", "haiku"),
    ("claude-opus-4-8", "opus"),
    ("claude-sonnet-4-6", "sonnet"),
    ("claude-haiku-4-5", "haiku"),
    ("", "sonnet"),
    ("   ", "sonnet"),
    ("some-unknown-model", "sonnet"),
])
def test_tier_for_model(model, tier):
    assert _tier_for_model(model) == tier


def test_glm_dot_flagship_is_opus_label_migration_proof():
    # The migration-proofing invariant: ANY glm-5.x dot-flagship labels as opus,
    # so a version bump (5.1 -> 5.2 -> 5.3 ...) can never mis-label as sonnet.
    # (Now an informational label only; the adapter no longer caps on tier.)
    for v in ("glm-5.1", "glm-5.2", "glm-5.9", "glm-5.10"):
        assert _tier_for_model(v) == "opus"
