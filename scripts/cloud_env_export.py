"""Emit shell-quoted `export KEY=...` lines for Claude Code on the web sessions.

Run by `.claude/hooks/session-start.sh` ONLY in cloud sessions
(``CLAUDE_CODE_REMOTE=true``): stdout is appended to ``$CLAUDE_ENV_FILE``,
which the harness sources as a shell script before every subsequent Bash
command — so lines must be quoted ``export`` statements (bare KEY=value lines
don't export, and one unquoted special character aborts the whole source).

The Infisical machine-identity creds arrive via the claude.ai environment
config (there is no ``/opt/universal_agent/.env`` in the VM);
``initialize_runtime_secrets()`` reads them from ``os.environ``.

Exclusions:
- ``ANTHROPIC_*`` — interactive surface: the Max OAuth path must win, same
  rule as ``scripts/_claude_launcher.py``.
- ``GH_TOKEN`` / ``GITHUB_TOKEN`` — the cloud harness injects proxy
  placeholders deliberately; vault copies must not clobber them.
- multi-line values (PEM blocks, JSON blobs) — unrepresentable as one-line
  env entries; fetch those in-process via ``initialize_runtime_secrets()``.

Secret values flow only to stdout (destined for ``$CLAUDE_ENV_FILE``);
diagnostics go to stderr and name keys, never values. Fail-soft: always
exits 0 — a session without vault secrets beats a blocked session.
"""

from __future__ import annotations

import os
import shlex
import sys

_SKIP_KEYS = {"GH_TOKEN", "GITHUB_TOKEN"}
_SKIP_PREFIXES = ("ANTHROPIC_",)


def main() -> int:
    before = dict(os.environ)
    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets

        result = initialize_runtime_secrets(exclude_prefixes=_SKIP_PREFIXES)
        source = result.source
    except Exception as exc:  # noqa: BLE001 — hook is fail-soft by contract
        print(
            f"[cloud-env] WARNING: secret bootstrap failed ({type(exc).__name__}); nothing exported.",
            file=sys.stderr,
        )
        return 0

    emitted = 0
    skipped_multiline = 0
    for key in sorted(os.environ):
        value = os.environ[key]
        if before.get(key) == value:
            continue
        if key in _SKIP_KEYS or key.startswith(_SKIP_PREFIXES):
            continue
        if "\n" in value or "\r" in value:
            skipped_multiline += 1
            continue
        sys.stdout.write(f"export {key}={shlex.quote(value)}\n")
        emitted += 1

    print(
        f"[cloud-env] source={source} emitted={emitted} skipped_multiline={skipped_multiline}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
