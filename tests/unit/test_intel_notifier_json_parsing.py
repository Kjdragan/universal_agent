"""Equivalence tests for the A1b JSON-parser consolidation.

``claude_code_intel._parse_json_object`` (also imported by
``claude_code_intel_rollup``) and ``cron_artifact_notifier._parse_llm_json``
now delegate to the canonical ``utils.json_utils.extract_json_payload``.
These pin each module's local contract across the old parsers' input
envelope plus the tolerance the canonical parser adds.

``llm_classifier._parse_json_response`` is deliberately NOT migrated (its
raw_decode scan beats the canonical parser on stray-brace-in-prose input —
see the NOTE above it); its own tests in tests/test_llm_classifier.py pin
that behavior.
"""

from __future__ import annotations

import pytest

from universal_agent.services.claude_code_intel import _parse_json_object
from universal_agent.services.cron_artifact_notifier import _parse_llm_json


class TestParseJsonObject:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ('{"tier": "gold"}', {"tier": "gold"}),
            ('```json\n{"tier": "gold"}\n```', {"tier": "gold"}),
            ('```\n{"a": 1}\n```', {"a": 1}),
            # Canonical layers add tolerance the old bare json.loads lacked:
            ("{'single': 'quotes'}", {"single": "quotes"}),
            ('prose before {"a": 1}', {"a": 1}),
        ],
    )
    def test_parses_objects(self, text, expected):
        assert _parse_json_object(text) == expected

    def test_non_dict_json_returns_empty_dict(self):
        # Old contract: valid JSON that is not an object -> {}.
        assert _parse_json_object('[1, 2, 3]') == {}

    def test_unrecoverable_raises_for_caller_fallback(self):
        # Callers (classify path + rollup) wrap in try/except Exception and
        # fall back to heuristics; a raise here is the contract.
        with pytest.raises(ValueError):
            _parse_json_object("no json here whatsoever")


class TestParseLlmJson:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ('{"title": "x"}', {"title": "x"}),
            ('```json\n{"title": "x"}\n```', {"title": "x"}),
            ('{"trailing": 1,}', {"trailing": 1}),
        ],
    )
    def test_parses_objects(self, text, expected):
        assert _parse_llm_json(text) == expected

    @pytest.mark.parametrize("text", ["", "not json", "[1, 2]", "```\n```"])
    def test_never_raises_returns_empty_dict(self, text):
        # The notifier feeds the result straight into .get() chains — the
        # never-raises {} sentinel is load-bearing.
        assert _parse_llm_json(text) == {}
