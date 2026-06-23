"""Focused regression tests for the numeric coercion helpers
(``_safe_int`` / ``_safe_float`` / ``_env_int``) scattered across the runtime.

These helpers previously wrapped a single ``int()`` / ``float()`` call in a
broad ``except Exception:``. For a numeric coercion the only realistic failure
modes are ``ValueError`` (unparseable string) and ``TypeError`` (``None``,
list, dict, ...), so the broad catch was narrowed to
``except (ValueError, TypeError):``.

For the ``str | None`` typed helpers (``session_policy._env_int`` and
``logfire_payloads._safe_int``) the input is always stringified before the
conversion, so the caught set is unchanged there -- the tests below are pure
characterization (red-green does not apply).

For the ``typing.Any`` typed helpers (``task_hub._safe_int`` /
``_safe_float`` and ``work_threads._safe_int``) the narrowing is a genuine,
if tiny, behavior change: a value whose ``__int__`` / ``__float__`` raises an
*unexpected* exception (e.g. ``RuntimeError``) used to be silently swallowed
into the default and now propagates. That is the intended fail-fast behavior,
and the ``_RaisingInt`` / ``_RaisingFloat`` tests below provide the red-green
evidence (they fail under ``except Exception`` and pass under
``except (ValueError, TypeError)``).
"""
from __future__ import annotations

import pytest

from universal_agent.logfire_payloads import _safe_int as _lf_safe_int
from universal_agent.session_policy import _env_int
from universal_agent.task_hub import (
    _safe_float as _th_safe_float,
    _safe_int as _th_safe_int,
)
from universal_agent.work_threads import _safe_int as _wt_safe_int


class _RaisingInt:
    """Object whose ``__int__`` raises a non-(ValueError, TypeError) error."""

    def __int__(self) -> int:
        raise RuntimeError("boom-int")


class _RaisingFloat:
    """Object whose ``__float__`` raises a non-(ValueError, TypeError) error."""

    def __float__(self) -> float:
        raise RuntimeError("boom-float")


# --- task_hub._safe_int ---------------------------------------------------


def test_th_safe_int_parses_valid_string():
    assert _th_safe_int("42") == 42


def test_th_safe_int_returns_default_for_bad_string():
    assert _th_safe_int("not-a-number", default=7) == 7  # ValueError


def test_th_safe_int_returns_default_for_none():
    assert _th_safe_int(None, default=9) == 9  # TypeError: int(None)


def test_th_safe_int_returns_default_for_list():
    assert _th_safe_int([1, 2], default=9) == 9  # TypeError: int(list)


def test_th_safe_int_propagates_unexpected_exception():
    # A misbehaving __int__ signals a real bug and must NOT be swallowed.
    with pytest.raises(RuntimeError):
        _th_safe_int(_RaisingInt())


# --- task_hub._safe_float -------------------------------------------------


def test_th_safe_float_parses_valid_string():
    assert _th_safe_float("1.5") == 1.5


def test_th_safe_float_returns_default_for_bad_string():
    assert _th_safe_float("nope", default=2.5) == 2.5  # ValueError


def test_th_safe_float_returns_default_for_none():
    assert _th_safe_float(None, default=3.5) == 3.5  # TypeError: float(None)


def test_th_safe_float_propagates_unexpected_exception():
    with pytest.raises(RuntimeError):
        _th_safe_float(_RaisingFloat())


# --- work_threads._safe_int -----------------------------------------------


def test_wt_safe_int_parses_valid_string():
    assert _wt_safe_int("3") == 3


def test_wt_safe_int_clamps_to_minimum_one():
    assert _wt_safe_int("0") == 1  # max(1, int("0")) == 1


def test_wt_safe_int_returns_default_for_bad_string():
    assert _wt_safe_int("garbage", default=5) == 5  # ValueError


def test_wt_safe_int_returns_default_for_none():
    assert _wt_safe_int(None, default=5) == 5  # TypeError


def test_wt_safe_int_propagates_unexpected_exception():
    with pytest.raises(RuntimeError):
        _wt_safe_int(_RaisingInt())


# --- session_policy._env_int (characterization only) ----------------------


def test_env_int_reads_env_value(monkeypatch):
    monkeypatch.setenv("UA_TEST_ENV_INT", "42")
    assert _env_int("UA_TEST_ENV_INT", 1) == 42


def test_env_int_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("UA_TEST_ENV_INT", raising=False)
    assert _env_int("UA_TEST_ENV_INT", 99) == 99


def test_env_int_returns_default_for_empty(monkeypatch):
    monkeypatch.setenv("UA_TEST_ENV_INT", "")
    assert _env_int("UA_TEST_ENV_INT", 99) == 99


def test_env_int_returns_default_for_garbage(monkeypatch):
    monkeypatch.setenv("UA_TEST_ENV_INT", "abc")
    assert _env_int("UA_TEST_ENV_INT", 99) == 99  # ValueError


# --- logfire_payloads._safe_int (characterization only) -------------------


def test_lf_safe_int_parses_valid_string():
    assert _lf_safe_int("5", default=0, min_value=0, max_value=100) == 5


def test_lf_safe_int_returns_default_for_bad_string():
    assert _lf_safe_int("x", default=7, min_value=0, max_value=100) == 7


def test_lf_safe_int_returns_default_for_none():
    assert _lf_safe_int(None, default=7, min_value=0, max_value=100) == 7


def test_lf_safe_int_clamps_to_range():
    assert _lf_safe_int("500", default=0, min_value=0, max_value=100) == 100
    assert _lf_safe_int("-5", default=0, min_value=0, max_value=100) == 0
