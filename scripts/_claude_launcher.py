"""Python helper invoked by `scripts/claude_with_mcp_env.sh`.

Sources $UA_INSTALL_ROOT/.env (bootstrap creds), calls
`initialize_runtime_secrets()` to pull Infisical secrets onto os.environ
(excluding ANTHROPIC_*), then execvp's `claude` so the bootstrapped env
is fully inherited by Claude Code and its MCP children.

Naming: leading underscore signals "internal helper, not intended to
be invoked directly by operators" — use the bash wrapper which sets
up the uv environment first.

Why this excludes ANTHROPIC_* from the load
-------------------------------------------
Phase A of the interactive-coding inversion staged ZAI routing vars
(ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_DEFAULT_*_MODEL)
in Infisical so UA Python services pick them up at startup. UA also
keeps ANTHROPIC_API_KEY there for direct-SDK code paths
(refinement_agent, gateway_server vision endpoint, etc.).

This launcher is the *interactive* path. Phase B's whole point was
that interactive `claude` defaults to Anthropic Max via OAuth. Any
ANTHROPIC_* key on os.environ overrides OAuth: ANTHROPIC_API_KEY
makes Claude Code treat it as an "external API key" (yields the
"Invalid API key · Fix external API key" UI when that key is for a
different account / has no Max billing); ANTHROPIC_BASE_URL routes to
ZAI/GLM. Both are wrong for interactive use.

Fix: pass `exclude_prefixes=("ANTHROPIC_",)` to
initialize_runtime_secrets() so the entire ANTHROPIC_* namespace is
filtered out at the Infisical-injection step (vars never enter
os.environ). UA Python services that need those keys call the same
function without the exclude param. The post-bootstrap strip below is
defense-in-depth against any other source (bootstrap .env file,
parent shell leak). Explicit ZAI opt-in remains via the `zai` shell
function. See
docs/06_Deployment_And_Environments/10_Interactive_Coding_Environment.md.

Why this also strips GH_TOKEN / GITHUB_TOKEN
--------------------------------------------
The same Infisical-injection pattern was poisoning interactive `gh`:
Infisical holds a `GH_TOKEN` value that's currently invalid/expired,
and the `gh` CLI checks env-var auth (GH_TOKEN, then GITHUB_TOKEN)
before falling back to file-stored OAuth (~/.config/gh/hosts.yml).
With a stale GH_TOKEN on os.environ, every `gh` call inside an
interactive Claude session failed with "The token in GH_TOKEN is
invalid" — which broke `/ship`'s in-script deploy watching.

The file-stored OAuth (`gho_*` token in hosts.yml) is the credential
we actually want for interactive sessions. Stripping GH_TOKEN /
GITHUB_TOKEN from interactive launches lets `gh` resolve to that.
UA Python services and crons keep getting the env var (they don't go
through this launcher), so anything depending on Infisical's GH_TOKEN
value continues to behave the way it did before.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

# Any os.environ key starting with this prefix is removed before
# exec'ing `claude`, so OAuth (~/.claude/.credentials.json) is the
# auth path the CLI resolves to. The matching prefix is also passed
# to `initialize_runtime_secrets(exclude_prefixes=…)` so the vars
# don't enter os.environ from Infisical in the first place.
_INTERACTIVE_STRIP_PREFIX: str = "ANTHROPIC_"

# Exact env-var names removed before exec'ing `claude`. Same intent as
# _INTERACTIVE_STRIP_PREFIX but for vars where prefix matching is too
# broad. GH_TOKEN/GITHUB_TOKEN: gh CLI checks these before file-stored
# OAuth (~/.config/gh/hosts.yml); a stale Infisical GH_TOKEN was breaking
# every interactive `gh` call (and therefore /ship's in-script deploy
# watching). Crons/services still see these via the normal Infisical load.
_INTERACTIVE_STRIP_NAMES: tuple[str, ...] = ("GH_TOKEN", "GITHUB_TOKEN")


def _strip_interactive_routing_vars(env: dict[str, str]) -> list[str]:
    """Remove every ANTHROPIC_* key from `env` in place; return removed keys.

    Pure helper so it can be unit-tested without execvp side effects.
    Defense-in-depth against any non-Infisical source that might leak an
    ANTHROPIC_* var into the launcher's env (bootstrap .env file, parent
    shell, etc.).
    """
    keys = sorted(k for k in list(env.keys()) if k.startswith(_INTERACTIVE_STRIP_PREFIX))
    for key in keys:
        env.pop(key, None)
    return keys


def _strip_named_interactive_vars(env: dict[str, str]) -> list[str]:
    """Remove every key in _INTERACTIVE_STRIP_NAMES from `env` in place.

    Returns the names actually removed (subset of the constant) so the
    launcher can log a precise diagnostic. Counterpart to
    _strip_interactive_routing_vars but for exact-name matches rather
    than prefix matches.
    """
    removed = sorted(name for name in _INTERACTIVE_STRIP_NAMES if name in env)
    for name in removed:
        env.pop(name, None)
    return removed


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
        # Filter ANTHROPIC_* at injection time so the vars never land on
        # os.environ for this launcher process. Anthropic Max OAuth
        # (~/.claude/.credentials.json) becomes the resolved auth path.
        result = initialize_runtime_secrets(
            exclude_prefixes=(_INTERACTIVE_STRIP_PREFIX,),
        )
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
        f"(env={os.environ.get('INFISICAL_ENVIRONMENT', 'unknown')}, "
        f"{_INTERACTIVE_STRIP_PREFIX}* excluded); launching claude…",
        file=sys.stderr,
    )

    # Defense-in-depth: catch any ANTHROPIC_* leaked from a non-Infisical
    # source (bootstrap .env file, parent shell, etc.). The Infisical-side
    # exclude_prefixes filter handles the dominant case; this catches the rest.
    stripped = _strip_interactive_routing_vars(os.environ)
    if stripped:
        print(
            f"🧹 stripped {len(stripped)} leaked {_INTERACTIVE_STRIP_PREFIX}* "
            f"var(s) from non-Infisical source: {', '.join(stripped)}",
            file=sys.stderr,
        )

    # Same intent for exact-name matches (currently GH_TOKEN / GITHUB_TOKEN).
    # Stale Infisical GH_TOKEN was overriding the file-stored OAuth and
    # breaking every interactive `gh` call.
    stripped_named = _strip_named_interactive_vars(os.environ)
    if stripped_named:
        print(
            f"🧹 stripped {len(stripped_named)} interactive-only "
            f"var(s) from os.environ: {', '.join(stripped_named)}",
            file=sys.stderr,
        )

    # execvp claude so the user's terminal directly drives the process.
    # All secrets are already on os.environ and will be inherited.
    args = ["claude", *sys.argv[1:]]
    os.execvp("claude", args)
    return 1  # unreachable


if __name__ == "__main__":
    sys.exit(main())
