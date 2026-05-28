"""Regression test for the asyncio StreamReader limit on the spawned claude CLI.

Background — `_monitor_cli_output` in `claude_cli_client.py` reads the CLI's
`stream-json` output one line at a time via `proc.stdout.readline()`. The
subprocess was originally created with no `limit=` kwarg, which means the
asyncio default of 64 KiB applied. The Claude CLI legitimately emits single
stream-json lines well above 64 KiB (large `tool_result` blocks from file
reads/web fetches, large assistant text). When that happened, `readline()`
overran the buffer and raised an exception — internally an
`asyncio.LimitOverrunError`, surfaced to the caller as a `ValueError` (see
`asyncio/streams.py:StreamReader.readline`). Either way it crosses the broad
`except Exception` in `_monitor_cli_output` and translates into a
`vp_mission_failure` row with message ``"Error monitoring CLI: <exc>"`` — a
real production failure mode observed 2026-05-27 / 2026-05-28.

The fix is a single kwarg: `limit=CLI_STREAM_BUFFER_LIMIT` (10 MiB) passed to
`asyncio.create_subprocess_exec`. This test exercises `create_subprocess_exec`
directly with both configurations to:

1. Reproduce the bug — confirm `readline()` raises when the default 64 KiB
   limit applies and the subprocess emits a >64 KiB line.
2. Verify the fix — confirm `readline()` returns the full payload cleanly when
   the 10 MiB limit is in effect.

We intentionally drive the subprocess primitive itself rather than monkey-
patching `_monitor_cli_output`, because the bug lives in the StreamReader
created by `create_subprocess_exec` — not in any code we wrote.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from universal_agent.vp.clients.claude_cli_client import CLI_STREAM_BUFFER_LIMIT

# A line large enough to overrun asyncio's default 64 KiB StreamReader buffer,
# but well under our 10 MiB ceiling, so the fixed configuration handles it
# without trouble.
_LINE_LEN = 200_000


def _emit_long_line_cmd() -> list[str]:
    """Return argv that prints a single `_LINE_LEN`-byte line + newline to stdout."""
    return [
        sys.executable,
        "-c",
        f"import sys; sys.stdout.write('x' * {_LINE_LEN} + chr(10)); sys.stdout.flush()",
    ]


def test_constant_is_ten_mib() -> None:
    """Sanity check — the buffer constant is what the fix advertises."""
    assert CLI_STREAM_BUFFER_LIMIT == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_default_limit_raises_on_oversized_line() -> None:
    """Reproduces the production bug: default 64 KiB buffer cannot hold a 200 KB line.

    `StreamReader.readline` catches the internal `LimitOverrunError` and re-raises
    as `ValueError` ("Separator is not found, and chunk exceed the limit"). Both
    are subclasses of `Exception`, so the broad `except Exception` in
    `_monitor_cli_output` swallows it as "Error monitoring CLI" either way.
    """
    proc = await asyncio.create_subprocess_exec(
        *_emit_long_line_cmd(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert proc.stdout is not None
        with pytest.raises((ValueError, asyncio.LimitOverrunError)) as exc_info:
            await proc.stdout.readline()
        # The error message asyncio raises mentions the buffer limit — confirm
        # this is the overrun path and not some unrelated ValueError.
        assert "limit" in str(exc_info.value).lower()
    finally:
        await proc.wait()


@pytest.mark.asyncio
async def test_ten_mib_limit_reads_large_line_cleanly() -> None:
    """Verifies the fix: 10 MiB buffer handles a 200 KB line end-to-end."""
    proc = await asyncio.create_subprocess_exec(
        *_emit_long_line_cmd(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=CLI_STREAM_BUFFER_LIMIT,
    )
    try:
        assert proc.stdout is not None
        line = await proc.stdout.readline()
        # Full payload + trailing newline survives the buffer.
        assert len(line) == _LINE_LEN + 1
        assert line.endswith(b"\n")
        assert line[:-1] == b"x" * _LINE_LEN
    finally:
        await proc.wait()
