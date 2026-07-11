"""Shared environment-variable parsing helpers.

Consolidates the ~20 copy-pasted ``_env_flag`` / ``_env_bool`` /
``_env_int`` helpers and ``_TRUTHY`` set literals that had drifted across
services (2026-07-11 style pass). Two boolean semantics exist deliberately:
``env_flag`` (2-state: junk values are False) and ``env_flag_3state``
(junk values fall back to the default) — migrate to whichever matches the
call site's original behavior.
"""

from __future__ import annotations

import os
from typing import Optional

TRUTHY = {"1", "true", "yes", "on"}
FALSY = {"0", "false", "no", "off"}


def env_flag(name: str, default: bool = False) -> bool:
    """2-state: empty/unset -> default; else membership in TRUTHY."""
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in TRUTHY


def env_flag_3state(name: str, default: bool = False) -> bool:
    """3-state: TRUTHY -> True, FALSY -> False, anything else -> default."""
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    if raw in TRUTHY:
        return True
    if raw in FALSY:
        return False
    return default


def env_int(name: str, default: int, *, minimum: Optional[int] = None) -> int:
    """Int env var; unparseable/empty -> default; optional lower clamp."""
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if minimum is not None and value < minimum:
        return minimum
    return value
