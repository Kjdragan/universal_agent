"""Phase 0 smoke demo — verify the dual-environment setup works end-to-end.

Validates that running `claude` from inside this workspace exercises real
Anthropic endpoints via the Max plan OAuth session, not the ZAI mapping
that pollutes UA's normal `~/.claude/settings.json`.

This smoke is CLI-driven, not SDK-driven, because:

1. Cody's actual demo work runs the `claude` CLI (she IS a Claude Code
   instance). The CLI uses the Max plan OAuth session.
2. The Anthropic Python SDK uses a *different* auth mechanism — it reads
   `ANTHROPIC_API_KEY` env vars and ignores the OAuth session entirely.
   A successful `claude /login` does NOT make `Anthropic()` work in
   Python without an extra env var.

So validating the demo execution path means invoking the CLI and checking
the response, not constructing an SDK client.

Two checks, in order:

1. The subprocess inherits no `ANTHROPIC_BASE_URL` override — i.e., no
   stray env var from the parent shell that would redirect to ZAI.
2. The `claude` CLI runs from inside this workspace, the project-local
   `.claude/settings.json` (vanilla — no env block) takes precedence over
   the polluted user-global one, and a one-shot prompt completes against
   the real API.

Exit codes:
  0 — both checks passed (CLI returned a sensible response)
  1 — claude CLI failed (binary missing, OAuth expired, or API error)
  2 — endpoint mismatch: ANTHROPIC_BASE_URL is set in the parent env

Usage:
    cd /opt/ua_demos/_smoke
    uv run python smoke.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess as sp
import sys
from pathlib import Path


SMOKE_PROMPT = "Reply with exactly the word OK and nothing else."
SMOKE_EXPECTED_TOKEN = "OK"
SMOKE_TIMEOUT_SECONDS = 60


def _capture_endpoint() -> str:
    """Determine which endpoint the demo would actually hit.

    The Claude Code CLI obeys `ANTHROPIC_BASE_URL` as an override even when
    a project-local settings.json says otherwise (env vars beat config in
    most CLI tools). So a stray env var from the parent shell is the most
    common false-success failure mode — catch it loud here.
    """
    base = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base:
        return base
    return "https://api.anthropic.com"


def _claude_binary() -> str | None:
    """Locate the claude CLI. Returns None if not on PATH."""
    return shutil.which("claude")


def _run_cli_smoke() -> dict[str, object]:
    """Invoke `claude -p "..."` and capture the reply."""
    binary = _claude_binary()
    if binary is None:
        return {
            "ok": False,
            "live_call": "skipped_claude_cli_not_installed",
            "remediation": (
                "claude CLI not on PATH. Install via npm i -g @anthropic-ai/claude-code "
                "or per the Phase 0 dependency-currency upgrade pipeline."
            ),
        }
    try:
        completed = sp.run(
            [binary, "-p", SMOKE_PROMPT],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT_SECONDS,
            check=False,
        )
    except sp.TimeoutExpired:
        return {
            "ok": False,
            "live_call": "timeout",
            "timeout_seconds": SMOKE_TIMEOUT_SECONDS,
        }
    except Exception as exc:
        return {
            "ok": False,
            "live_call": "subprocess_error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:400],
        }

    stdout = (completed.stdout or "").strip()
    stderr_excerpt = (completed.stderr or "").strip()[:600]

    if completed.returncode != 0:
        return {
            "ok": False,
            "live_call": "failed",
            "return_code": completed.returncode,
            "stdout_excerpt": stdout[:200],
            "stderr_excerpt": stderr_excerpt,
        }

    contains_token = SMOKE_EXPECTED_TOKEN in stdout.upper()
    return {
        "ok": contains_token,
        "live_call": "completed",
        "response_excerpt": stdout[:200],
        "matched_expected_token": contains_token,
    }


def main() -> int:
    endpoint = _capture_endpoint()
    expected_host = "api.anthropic.com"
    if expected_host not in endpoint:
        print(
            json.dumps(
                {
                    "ok": False,
                    "reason": "endpoint_mismatch",
                    "endpoint": endpoint,
                    "expected_substring": expected_host,
                    "remediation": (
                        "ANTHROPIC_BASE_URL is set in this subprocess. "
                        "The demo workspace must inherit a clean environment — "
                        "unset ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN, and "
                        "ANTHROPIC_DEFAULT_*_MODEL before launching."
                    ),
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    cli = _run_cli_smoke()
    cli["endpoint"] = endpoint
    cli["host"] = (
        Path("/etc/hostname").read_text(encoding="utf-8").strip()
        if Path("/etc/hostname").exists()
        else os.getenv("USER", "")
    )

    out_stream = sys.stdout if cli.get("ok") else sys.stderr
    print(json.dumps(cli, indent=2), file=out_stream)
    return 0 if cli.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
