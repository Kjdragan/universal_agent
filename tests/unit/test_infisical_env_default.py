"""The Infisical environment default must be host-aware.

On the production VPS an *unset* INFISICAL_ENVIRONMENT should resolve to
"production" (the VPS is production-only), not "development" — silently loading
dev secrets on the prod box is a recurring footgun (toggles like
UA_AGENTMAIL_ENABLED default off in dev, so a digest "sends" but mail is disabled).
An explicit value always wins.
"""

from __future__ import annotations

from universal_agent import infisical_loader


def test_unset_defaults_to_production_on_vps(monkeypatch):
    monkeypatch.setattr(infisical_loader, "_on_production_vps", lambda: True)
    assert infisical_loader._normalize_infisical_environment(None) == "production"
    assert infisical_loader._normalize_infisical_environment("") == "production"
    assert infisical_loader._normalize_infisical_environment("   ") == "production"


def test_unset_defaults_to_development_off_vps(monkeypatch):
    monkeypatch.setattr(infisical_loader, "_on_production_vps", lambda: False)
    assert infisical_loader._normalize_infisical_environment(None) == "development"
    assert infisical_loader._normalize_infisical_environment("") == "development"


def test_explicit_value_always_wins_even_on_vps(monkeypatch):
    # An operator who explicitly wants dev secrets on the prod box must still get them.
    monkeypatch.setattr(infisical_loader, "_on_production_vps", lambda: True)
    assert infisical_loader._normalize_infisical_environment("development") == "development"
    assert infisical_loader._normalize_infisical_environment("staging") == "staging"


def test_legacy_aliases_resolved(monkeypatch):
    monkeypatch.setattr(infisical_loader, "_on_production_vps", lambda: False)
    assert infisical_loader._normalize_infisical_environment("prod") == "production"
    assert infisical_loader._normalize_infisical_environment("dev") == "development"
