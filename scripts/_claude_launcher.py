"""Python helper invoked by `scripts/claude_with_mcp_env.sh`.

Sources $UA_INSTALL_ROOT/.env (bootstrap creds), calls
`initialize_runtime_secrets()` to pull every Infisical secret onto
os.environ, then execvp's `claude` so the bootstrapped env is fully
inherited by Claude Code and its MCP children.

Naming: leading underscore signals "internal helper, not intended to
be invoked directly by operators" — use the bash wrapper which sets
up the uv environment first.

Why this strips ANTHROPIC_* before exec'ing claude
--------------------------------------------------
Phase A of the interactive-coding inversion staged 5 ZAI routing vars
(ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_DEFAULT_*_MODEL)
in Infisical so UA Python services pick them up at startup. That is
the correct routing for autonomous agent runs.

But this launcher is the *interactive* path. Phase B's whole point
was that interactive `claude` defaults to Anthropic Max via OAuth,
not ZAI. Because `initialize_runtime_secrets()` fetches every
Infisical secret indiscriminately, those ZAI vars land on os.environ
and would silently re-route interactive `claude` back through ZAI —
defeating the inversion. The fix is to strip just those keys after
the bootstrap and before execvp; explicit ZAI opt-in remains
available via the `zai` shell function (which uses `infisical run`,
not this launcher). See
docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

# Keys whose presence in os.environ forces the Claude CLI to route
# LLM calls through ZAI/GLM. Stripping them lets OAuth → Anthropic
# Max take over for the interactive launcher's exec'd `claude`.
_INTERACTIVE_STRIP_KEYS: tuple[str, ...] = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
)


def _strip_interactive_routing_vars(env: dict[str, str]) -> list[str]:
    """Remove ZAI routing vars from `env` in place; return the keys removed.

    Pure helper so it can be unit-tested without execvp side effects.
    """
    return [k for k in _INTERACTIVE_STRIP_KEYS if env.pop(k, None) is not None]


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

    stripped = _strip_interactive_routing_vars(os.environ)
    if stripped:
        print(
            f"🧹 unset {len(stripped)} ZAI routing var(s) so interactive "
            "claude defaults to Anthropic Max OAuth — use `zai` for cheap GLM",
            file=sys.stderr,
        )

    # execvp claude so the user's terminal directly drives the process.
    # All secrets are already on os.environ and will be inherited.
    args = ["claude", *sys.argv[1:]]
    os.execvp("claude", args)
    return 1  # unreachable


if __name__ == "__main__":
    sys.exit(main())
