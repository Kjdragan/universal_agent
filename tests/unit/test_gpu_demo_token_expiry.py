"""GPU-demo approval tokens must expire (and old non-expiring tokens must be
rejected). An emailed one-click approve/reject link that leaks or is forwarded
must not stay actionable forever; the token now embeds an expiry
(``{exp}.{sig}``) that verify checks.
"""

import pytest

from universal_agent.services import cron_artifact_notifier as can


@pytest.fixture(autouse=True)
def _ack_secret(monkeypatch):
    monkeypatch.setenv("UA_ARTIFACT_ACK_SECRET", "test-ack-secret")


def test_round_trip_valid():
    tok = can.sign_gpu_demo_token("task-1", "approve")
    assert "." in tok  # {exp}.{sig}
    assert can.verify_gpu_demo_token("task-1", "approve", tok) is True


def test_action_bound():
    # An approve token must not authorize a reject (and vice-versa).
    tok = can.sign_gpu_demo_token("task-1", "approve")
    assert can.verify_gpu_demo_token("task-1", "reject", tok) is False


def test_task_bound():
    tok = can.sign_gpu_demo_token("task-1", "approve")
    assert can.verify_gpu_demo_token("task-2", "approve", tok) is False


def test_expired_rejected():
    tok = can.sign_gpu_demo_token("task-1", "approve", ttl_seconds=-10)
    assert can.verify_gpu_demo_token("task-1", "approve", tok) is False


def test_legacy_nonexpiring_token_rejected():
    # A bare 16-hex HMAC (the old format, no expiry) must no longer validate.
    assert can.verify_gpu_demo_token("task-1", "approve", "abcdef0123456789") is False


def test_bad_action_never_signs():
    assert can.sign_gpu_demo_token("task-1", "delete") == ""


def test_empty_without_secret(monkeypatch):
    for var in ("UA_ARTIFACT_ACK_SECRET", "UA_OPS_TOKEN", "UA_INTERNAL_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert can.sign_gpu_demo_token("task-1", "approve") == ""
    assert can.verify_gpu_demo_token("task-1", "approve", "123.abc") is False
