"""Equivalence tests for the mission_control ``_extract_json_object`` shims.

Both ``mission_control_tier1`` and ``mission_control_chief_of_staff`` carried
byte-identical hand-rolled JSON parsers; they now delegate to the canonical
``utils.json_utils.extract_json_payload``. These tests pin the local contract
the call sites rely on (dict-or-raise) across every input shape the old
parser handled, plus the tolerance the canonical parser adds.
"""

from __future__ import annotations

import pytest

from universal_agent.services import (
    mission_control_chief_of_staff as cos,
    mission_control_tier1 as tier1,
)

PARSERS = [tier1._extract_json_object, cos._extract_json_object]

# (input, expected) pairs the OLD parser already handled — behavior must hold.
EQUIVALENCE_CASES = [
    ('{"cards": []}', {"cards": []}),
    ('```json\n{"cards": [1]}\n```', {"cards": [1]}),
    ('```\n{"a": true}\n```', {"a": True}),
    ('Here you go:\n{"a": 1} thanks', {"a": 1}),
]

# Inputs the OLD parser raised on that the canonical parser now recovers.
NEW_TOLERANCE_CASES = [
    ("{'single': 'quotes'}", {"single": "quotes"}),
    ('{"trailing": 1,}', {"trailing": 1}),
    ('{"python": True, "null": None}', {"python": True, "null": None}),
]


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize("text,expected", EQUIVALENCE_CASES)
def test_old_parser_inputs_still_parse(parser, text, expected):
    assert parser(text) == expected


@pytest.mark.parametrize("parser", PARSERS)
@pytest.mark.parametrize("text,expected", NEW_TOLERANCE_CASES)
def test_canonical_layers_add_tolerance(parser, text, expected):
    assert parser(text) == expected


@pytest.mark.parametrize("parser", PARSERS)
def test_unrecoverable_input_raises_valueerror(parser):
    # Callers wrap in try/except Exception; ValueError (the canonical
    # parser's failure mode) satisfies that contract.
    with pytest.raises(ValueError):
        parser("total garbage with no braces at all")


@pytest.mark.parametrize("parser", PARSERS)
def test_non_object_json_raises_instead_of_leaking_a_list(parser):
    # The old parser's annotation said dict but a bare JSON list leaked
    # through and AttributeError'd later at `.get()` OUTSIDE the caller's
    # try (tier1). The shim raises inside the parse step, which the callers
    # catch — a strict robustness improvement, pinned here.
    with pytest.raises(ValueError):
        parser('["a", "b"]')
