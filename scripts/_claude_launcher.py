"""Python helper invoked by `scripts/claude_with_mcp_env.sh`.

Sources $UA_INSTALL_ROOT/.env (bootstrap creds), calls
`initialize_runtime_secrets()` to pull every Infisical secret onto
os.environ, then execvp's `claude` so the bootstrapped env is fully
inherited by Claude Code and its MCP children.

Naming: leading underscore signals "internal helper, not intended to
be invoked directly by operators" — use the bash wrapper which sets
up the uv environment first.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _source_env_file(path: Path) -> int:
    """Read .env-style file and inject KEY=VALUE pairs into os.environ.

    Existing env vars are NOT overwritten (matches `set -a; source` semantics
    where the shell's existing env wins on conflict — but in practice this
    file holds bootstrap creds the shell hasn't seen, so it's a clean
    insert). Returns the count of vars injected.
    """
    if not path.is_file():
        return 0
    injected = 0
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        # Strip surrounding quotes if present (.env convention)
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (
            val.startswith("'") and val.endswith("'")
        ):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val
            injected += 1
    return injected


def main() -> int:
    install_root = Path(os.environ.get("UA_INSTALL_ROOT", "/opt/universal_agent"))
    env_file = install_root / ".env"

    injected = _source_env_file(env_file)
    print(
        f"🔑 sourced {injected} bootstrap var(s) from {env_file}",
        file=sys.stderr,
    )

    # Make sure UA's src/ is on sys.path so we can import the loader from the
    # production checkout regardless of where this script lives.
    src_dir = str(install_root / "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    try:
        from universal_agent.infisical_loader import initialize_runtime_secrets
    except ImportError as exc:
        print(
            f"❌ Could not import infisical_loader from {src_dir}: {exc}",
            file=sys.stderr,
        )
        return 2

    try:
        result = initialize_runtime_secrets()
    except Exception as exc:  # noqa: BLE001 — we want the operator to see *why*
        print(f"❌ initialize_runtime_secrets() failed: {exc}", file=sys.stderr)
        return 3

    if result.source != "infisical":
        print(
            f"⚠️  bootstrap source={result.source} (expected 'infisical') — "
            "MCP servers needing Infisical-only secrets may still fail",
            file=sys.stderr,
        )
    print(
        f"🔓 Infisical bootstrap loaded {result.loaded_count} secret(s) "
        f"(env={os.environ.get('INFISICAL_ENVIRONMENT', 'unknown')}); "
        "launching claude…",
        file=sys.stderr,
    )

    # execvp claude so the user's terminal directly drives the process.
    # All secrets are already on os.environ and will be inherited.
    args = ["claude", *sys.argv[1:]]
    os.execvp("claude", args)
    return 1  # unreachable


if __name__ == "__main__":
    sys.exit(main())
