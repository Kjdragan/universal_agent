"""Regression guards for the Telegram channel token in interactive `claude` launch.

The Claude Code telegram plugin is a per-session getUpdates poller and Telegram
allows exactly ONE consumer per bot token. Infisical's production
``TELEGRAM_BOT_TOKEN`` is the *Universal Agent* bot, and
``initialize_runtime_secrets`` injects it with ``overwrite=True`` — so before
``_reconcile_channel_token`` every interactive ``claudereal`` session became a
second poller on UA's bot. That produced a week-long 409 storm (10,577
conflicts) which left the VPS bot deaf.

It does not self-correct, either: the losing poller's retry loop resets
``attempt = 0`` inside ``onStart``, so its give-up is unreachable and its
backoff computes to zero. It hammers forever and pins a CPU core once its
owning session exits.

The rule under test, therefore:

1. A token the *caller* deliberately supplied (``claude-tg-claim`` won the
   flock for this session) must survive the bootstrap's ``overwrite=True``.
2. A token the *vault* injected must be removed, so a session with no claim
   never polls. Its plugin server then exits at ``server.ts:45``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _import_launcher_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "_claude_launcher.py"
    spec = importlib.util.spec_from_file_location("_claude_launcher_ct", module_path)
    assert spec and spec.loader, f"could not build spec for {module_path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_claude_launcher_ct", module)
    spec.loader.exec_module(module)
    return module


_launcher = _import_launcher_module()
_reconcile_channel_token = _launcher._reconcile_channel_token
_CHANNEL_TOKEN_NAME = _launcher._CHANNEL_TOKEN_NAME

# Deliberately NOT shaped like a real Telegram token (which is
# <digits>:<35 chars>) so secret scanners do not flag these fixtures.
UA_BOT = "8331688448:fake-ua"
CLAIMED_BOT = "8862827743:fake-claimed"


def test_vault_injected_token_is_dropped_when_session_has_no_claim():
    """The dominant case: an ordinary session must not become a poller."""
    env = {_CHANNEL_TOKEN_NAME: UA_BOT, "OTHER": "keep"}
    note = _reconcile_channel_token(env, inherited=None)
    assert _CHANNEL_TOKEN_NAME not in env
    assert env["OTHER"] == "keep", "unrelated vars must be untouched"
    assert "dropped" in note


def test_claimed_token_survives_the_bootstrap_overwrite():
    """A session that won the flock keeps ITS bot, not the vault's."""
    # initialize_runtime_secrets(overwrite=True) has already clobbered the
    # caller's value with UA's by the time we run.
    env = {_CHANNEL_TOKEN_NAME: UA_BOT}
    note = _reconcile_channel_token(env, inherited=CLAIMED_BOT)
    assert env[_CHANNEL_TOKEN_NAME] == CLAIMED_BOT
    assert "8862827743" in note and "kept" in note


def test_no_token_anywhere_is_a_silent_no_op():
    """Absent from both vault and caller: nothing to say, nothing to do."""
    env = {"OTHER": "keep"}
    note = _reconcile_channel_token(env, inherited=None)
    assert env == {"OTHER": "keep"}
    assert note == ""


def test_reconcile_never_leaks_the_ua_bot_to_a_claimed_session():
    """Belt and braces: the UA bot id must not survive either branch."""
    for inherited in (None, CLAIMED_BOT):
        env = {_CHANNEL_TOKEN_NAME: UA_BOT}
        _reconcile_channel_token(env, inherited=inherited)
        assert env.get(_CHANNEL_TOKEN_NAME, "") != UA_BOT
