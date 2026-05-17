"""Resolve secrets that CSI cron scripts need when env-file values are empty.

Kept standalone (no csi_ingester package dependency) so it can be imported
from any CSI cron script AND unit-tested without installing csi_ingester
locally. Tests should monkeypatch `_fetch_infisical_secrets`.
"""

from __future__ import annotations


def _import_fetch_infisical_secrets():
    """Import lazily so test environments without universal_agent on sys.path
    can still exercise the rest of this module via monkeypatching."""
    from universal_agent.infisical_loader import _fetch_infisical_secrets

    return _fetch_infisical_secrets


def resolve_token_from_infisical(keys: list[str], *, log_prefix: str = "CSI_INFISICAL") -> str:
    """Pull the first non-empty secret named in `keys` from Infisical.

    Args:
        keys: Ordered preference list of secret names to try.
        log_prefix: Tag used for the stdout breadcrumb (e.g. RSS_ENRICH).

    Returns empty string if Infisical is unreachable or none of the keys
    have a value. Prints a single breadcrumb to stdout describing what
    happened — systemd will capture it in the journal.
    """
    try:
        fetch = _import_fetch_infisical_secrets()
    except Exception as exc:
        print(f"{log_prefix}_INFISICAL_IMPORT_FAIL detail={exc!r}")
        return ""
    try:
        secrets = fetch()
    except Exception as exc:
        print(f"{log_prefix}_INFISICAL_FETCH_FAIL detail={exc!r}")
        return ""
    for key in keys:
        value = str(secrets.get(key) or "").strip()
        if value:
            print(f"{log_prefix}_INFISICAL_TOKEN_SOURCE={key}")
            return value
    print(f"{log_prefix}_INFISICAL_NO_MATCH keys={','.join(keys)}")
    return ""
