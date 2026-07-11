"""Tests for the shared env-var parsing helpers (``utils/env_utils.py``).

Covers the two deliberate boolean semantics -- ``env_flag`` (2-state: junk
values are False) and ``env_flag_3state`` (junk values fall back to the
default) -- plus ``env_int``, and pins that a handful of migrated modules
now import the same function objects rather than carrying their own copies.
"""

from __future__ import annotations

from universal_agent.utils import env_utils
from universal_agent.utils.env_utils import env_flag, env_flag_3state, env_int


ENV_VAR = "UA_TEST_ENV_UTILS_FLAG"


class TestEnvFlagTwoState:
    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert env_flag(ENV_VAR, default=True) is True
        assert env_flag(ENV_VAR, default=False) is False

    def test_empty_string_returns_default(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "")
        assert env_flag(ENV_VAR, default=True) is True
        assert env_flag(ENV_VAR, default=False) is False

    def test_truthy_values(self, monkeypatch):
        for val in ("1", "true", "True", "YES", "on", "ON"):
            monkeypatch.setenv(ENV_VAR, val)
            assert env_flag(ENV_VAR, default=False) is True

    def test_junk_value_is_false_regardless_of_default(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "banana")
        assert env_flag(ENV_VAR, default=True) is False
        assert env_flag(ENV_VAR, default=False) is False

    def test_falsy_spellings_are_false(self, monkeypatch):
        for val in ("0", "false", "no", "off"):
            monkeypatch.setenv(ENV_VAR, val)
            assert env_flag(ENV_VAR, default=True) is False


class TestEnvFlagThreeState:
    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv(ENV_VAR, raising=False)
        assert env_flag_3state(ENV_VAR, default=True) is True
        assert env_flag_3state(ENV_VAR, default=False) is False

    def test_truthy_values_are_true(self, monkeypatch):
        for val in ("1", "true", "Yes", "ON"):
            monkeypatch.setenv(ENV_VAR, val)
            assert env_flag_3state(ENV_VAR, default=False) is True

    def test_falsy_values_are_false(self, monkeypatch):
        for val in ("0", "false", "No", "OFF"):
            monkeypatch.setenv(ENV_VAR, val)
            assert env_flag_3state(ENV_VAR, default=True) is False

    def test_junk_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR, "banana")
        assert env_flag_3state(ENV_VAR, default=True) is True
        assert env_flag_3state(ENV_VAR, default=False) is False


class TestEnvInt:
    ENV_INT_VAR = "UA_TEST_ENV_UTILS_INT"

    def test_unset_returns_default(self, monkeypatch):
        monkeypatch.delenv(self.ENV_INT_VAR, raising=False)
        assert env_int(self.ENV_INT_VAR, 42) == 42

    def test_empty_returns_default(self, monkeypatch):
        monkeypatch.setenv(self.ENV_INT_VAR, "")
        assert env_int(self.ENV_INT_VAR, 42) == 42

    def test_valid_int_parses(self, monkeypatch):
        monkeypatch.setenv(self.ENV_INT_VAR, "7")
        assert env_int(self.ENV_INT_VAR, 42) == 7

    def test_unparseable_returns_default(self, monkeypatch):
        monkeypatch.setenv(self.ENV_INT_VAR, "not-a-number")
        assert env_int(self.ENV_INT_VAR, 42) == 42

    def test_minimum_clamp_applies_to_parsed_value(self, monkeypatch):
        monkeypatch.setenv(self.ENV_INT_VAR, "-5")
        assert env_int(self.ENV_INT_VAR, 10, minimum=0) == 0

    def test_minimum_clamp_does_not_apply_to_default(self, monkeypatch):
        # Empty/unset short-circuits to the default before the minimum
        # clamp is ever considered -- matches the documented contract.
        monkeypatch.delenv(self.ENV_INT_VAR, raising=False)
        assert env_int(self.ENV_INT_VAR, -5, minimum=0) == -5

    def test_value_above_minimum_is_unaffected(self, monkeypatch):
        monkeypatch.setenv(self.ENV_INT_VAR, "3")
        assert env_int(self.ENV_INT_VAR, 10, minimum=1) == 3


class TestTruthyFalsySets:
    def test_content(self):
        assert env_utils.TRUTHY == {"1", "true", "yes", "on"}
        assert env_utils.FALSY == {"0", "false", "no", "off"}


class TestMigratedAliasesIdentity:
    """Pin that a handful of migrated modules import the SAME function
    objects rather than carrying their own re-implementation."""

    def test_notebooklm_runtime_alias(self):
        from universal_agent import notebooklm_runtime

        assert notebooklm_runtime._env_flag is env_flag_3state

    def test_arxiv_runtime_alias(self):
        from universal_agent import arxiv_runtime

        assert arxiv_runtime._env_flag is env_flag_3state

    def test_task_hub_alias(self):
        from universal_agent import task_hub

        assert task_hub._env_bool is env_flag_3state

    def test_session_policy_env_int_alias(self):
        from universal_agent import session_policy

        assert session_policy._env_int is env_int

    def test_execution_engine_env_flag_alias(self):
        from universal_agent import execution_engine

        assert execution_engine._env_truthy is env_flag
