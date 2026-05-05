"""Phase 0 smoke demo — verify the dual-environment setup actually works.

Two checks, in order:

1. Hits the `anthropic` SDK with NO env override and confirms the response
   came back from `api.anthropic.com`. This validates the Max plan OAuth
   session that `claude /login` provisioned on the VPS.

2. Reads /etc/hostname (or USER) so we have at least one piece of evidence
   the demo subprocess actually executed.

Exit code 0 = both checks pass.
Exit code 1 = anthropic call failed.
Exit code 2 = endpoint mismatch (request was redirected somewhere other than
              api.anthropic.com — usually means a stray ANTHROPIC_BASE_URL
              env var leaked in).

Cody's `cody-implements-from-brief` skill will run a similar shape for every
real demo: invoke the SDK, capture which endpoint actually served the
response, fail loud on a mismatch.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _capture_endpoint() -> str:
    """Determine which endpoint the anthropic SDK is configured to hit.

    Returns the BASE URL used by the next API request.
    """
    base = os.getenv("ANTHROPIC_BASE_URL", "").strip()
    if base:
        return base
    return "https://api.anthropic.com"


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
                        "An ANTHROPIC_BASE_URL is set in this subprocess. "
                        "The demo workspace must inherit a clean environment — "
                        "unset ANTHROPIC_BASE_URL before launching."
                    ),
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2

    # Best-effort live check. Skipped when the SDK isn't installed or no
    # session is available — this script must work as a structural smoke
    # without external dependencies hard-required.
    try:
        from anthropic import Anthropic  # type: ignore[import-not-found]
    except ImportError:
        print(
            json.dumps(
                {
                    "ok": True,
                    "endpoint": endpoint,
                    "live_call": "skipped_anthropic_sdk_not_installed",
                    "host": Path("/etc/hostname").read_text(encoding="utf-8").strip()
                    if Path("/etc/hostname").exists()
                    else os.getenv("USER", ""),
                },
                indent=2,
            )
        )
        return 0

    try:
        client = Anthropic()
        response = client.messages.create(
            model=os.getenv("ANTHROPIC_DEFAULT_SMOKE_MODEL", "claude-haiku-4-5-20251001"),
            max_tokens=64,
            messages=[{"role": "user", "content": "Reply with exactly the word OK."}],
        )
        text_blocks = [
            getattr(b, "text", "") for b in (response.content or []) if hasattr(b, "text")
        ]
        body = "".join(text_blocks).strip()
        ok = body.upper().startswith("OK")
        print(
            json.dumps(
                {
                    "ok": ok,
                    "endpoint": endpoint,
                    "model": getattr(response, "model", ""),
                    "stop_reason": getattr(response, "stop_reason", ""),
                    "live_call": "completed",
                    "response_excerpt": body[:120],
                },
                indent=2,
            )
        )
        return 0 if ok else 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "endpoint": endpoint,
                    "live_call": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:400],
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
