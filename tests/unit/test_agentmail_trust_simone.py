"""Simone's own AgentMail inbox is a trusted sender.

A Simone→Simone send (or an app sending as Simone) must be ingested + triaged,
not auto-quarantined as an unknown @agentmail.to sender — while genuinely unknown
@agentmail.to senders still quarantine.
"""

from universal_agent.services.agentmail_service import (
    _DEFAULT_TRUSTED_SENDERS,
    _trusted_sender_addresses,
)
from universal_agent.services.email_security import (
    should_auto_quarantine_agentmail_sender,
)

SIMONE = "oddcity216@agentmail.to"


def test_simone_address_in_default_trust_list():
    assert SIMONE in _DEFAULT_TRUSTED_SENDERS


def test_simone_resolves_as_trusted_when_env_unset(monkeypatch):
    monkeypatch.delenv("UA_AGENTMAIL_TRUSTED_SENDERS", raising=False)
    trusted = _trusted_sender_addresses()
    assert SIMONE in trusted
    # display-name form normalizes to the bare address, so it still matches
    assert not should_auto_quarantine_agentmail_sender(SIMONE, trusted)


def test_unknown_agentmail_sender_still_quarantines(monkeypatch):
    monkeypatch.delenv("UA_AGENTMAIL_TRUSTED_SENDERS", raising=False)
    trusted = _trusted_sender_addresses()
    assert should_auto_quarantine_agentmail_sender("stranger9000@agentmail.to", trusted)
