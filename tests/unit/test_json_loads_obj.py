"""Equivalence tests for the shared strict ``json_loads_obj`` helper.

Four byte-similar ``_json_loads_obj`` copies (proactive_tutorial_builds,
proactive_convergence, proactive_artifacts, proactive_signals) collapsed
onto ``utils.json_utils.json_loads_obj``. The helper is deliberately STRICT
(plain ``json.loads``, not ``extract_json_payload``'s repair layers): these
call sites parse DB/Task-Hub-stored JSON written by ``json.dumps``, where a
corrupt row must collapse to ``{}`` rather than be creatively repaired.
Call sites have NO local exception handling — never-raises is load-bearing.
"""

from __future__ import annotations

import pytest

from universal_agent.proactive_signals import _json_loads_obj as signals_alias
from universal_agent.services.proactive_artifacts import _json_loads_obj as artifacts_alias
from universal_agent.services.proactive_convergence import _json_loads_obj as convergence_alias
from universal_agent.services.proactive_tutorial_builds import _json_loads_obj as tutorial_alias
from universal_agent.utils.json_utils import json_loads_obj

ALL = [json_loads_obj, tutorial_alias, convergence_alias, artifacts_alias, signals_alias]


def test_all_modules_share_one_implementation():
    assert all(fn is json_loads_obj for fn in ALL)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"a": 1}', {"a": 1}),
        ({"already": "dict"}, {"already": "dict"}),
        ("", {}),
        (None, {}),
        (5, {}),
        ("[1, 2]", {}),          # valid JSON, not an object
        ('{"broken": ', {}),      # corrupt row -> {} (STRICT: no repair)
        ("{'single': 'quotes'}", {}),  # json.dumps never writes this; stay strict
        ("   ", {}),
    ],
)
def test_strict_dict_or_empty_contract(raw, expected):
    assert json_loads_obj(raw) == expected


def test_dict_input_returns_shallow_copy():
    src = {"k": "v"}
    out = json_loads_obj(src)
    assert out == src and out is not src


def test_unexpected_parse_errors_propagate_not_swallowed(monkeypatch):
    """Only ``json.JSONDecodeError`` should be tolerated — a genuinely
    unexpected error from ``json.loads`` must surface, not be silently
    flattened to ``{}`` by an over-broad ``except Exception``.

    The realistic failure mode of ``json.loads(non_empty_str)`` is only
    ``JSONDecodeError``; catching anything broader hides real bugs (e.g.
    a future regression in the parser path) behind an empty dict.
    """
    import universal_agent.utils.json_utils as ju

    def _boom(_raw):
        raise RuntimeError("unexpected non-JSON failure")

    monkeypatch.setattr(ju.json, "loads", _boom)
    with pytest.raises(RuntimeError):
        json_loads_obj('{"a": 1}')
