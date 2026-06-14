"""Tier resolution for the ProcessTurnAdapter wall-clock cap.

Regression guard for the Simone daemon stall (2026-06-14): after the glm-5.1→5.2
opus-map migration (2026-06-13), a daemon session still pinned to ``glm-5.1`` no
longer matched ``ZAI_MODEL_MAP['opus'] == 'glm-5.2'`` and fell through to the
sonnet 180s default. Every long Simone work turn was then killed at 180s — ~13h
with zero completed daemon turns (journal: ``wall-clock cap: 180s
(tier=sonnet, model='glm-5.1')`` → ``timed out after 180.0s``).
"""

import pytest

from universal_agent.execution_engine import _tier_for_model
from universal_agent.utils.model_resolution import model_call_timeout_seconds


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


def test_opus_cap_exceeds_sonnet_cap():
    # The whole point: opus turns get a much larger wall-clock cap than sonnet,
    # so a glm-5.1 Simone turn is no longer killed at the 180s sonnet default.
    assert model_call_timeout_seconds("opus") > model_call_timeout_seconds("sonnet")


def test_glm_5_1_gets_opus_cap_not_180s():
    # End-to-end of the fix: glm-5.1 -> opus tier -> opus cap (>> 180s).
    cap = model_call_timeout_seconds(_tier_for_model("glm-5.1"))
    assert cap > 180.0
