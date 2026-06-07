"""Regression guard: best-effort `except` blocks in ``hooks_service`` must keep
the caught exception's context when they log.

Theme: "improve error messages and logging context in except blocks." Several
``except Exception:`` handlers in :mod:`universal_agent.hooks_service` log a
``logger.warning("Failed to ...")`` describing *that* a best-effort step failed,
but historically dropped the exception itself — so the traceback (the *why*) was
lost. The fix adds ``exc_info=True`` (house idiom, preserves the WARNING level).

``HooksService._parse_dispatch_retry_policies`` is the cleanest representative
seam because it references no ``self`` state, so it can be exercised in isolation
with a dummy receiver. The other touched sites (marker writes/removes, the
executable-bit fix, local-ingest result/marker writes) apply the identical
mechanical transformation.
"""

import logging

from universal_agent.hooks_service import HooksService


def test_invalid_retry_policies_warning_retains_exception_context(monkeypatch, caplog):
    """Invalid ``UA_HOOKS_DISPATCH_RETRY_POLICIES`` JSON must still fall back to
    defaults AND emit a WARNING whose record carries the exception traceback."""
    monkeypatch.setenv("UA_HOOKS_DISPATCH_RETRY_POLICIES", "{not-valid-json")

    with caplog.at_level(logging.WARNING, logger="universal_agent.hooks_service"):
        # No `self` state is touched by this method, so a bare receiver is fine.
        result = HooksService._parse_dispatch_retry_policies(object())

    # Behaviour is unchanged: parse failure → documented defaults.
    assert result == {
        "hook_dispatch_failed": {
            "max_retries": 2,
            "delay_seconds": 120,
            "backoff_factor": 2.0,
        }
    }

    matching = [
        r
        for r in caplog.records
        if "UA_HOOKS_DISPATCH_RETRY_POLICIES" in r.getMessage()
    ]
    assert matching, "expected a warning about the invalid retry-policy env var"
    record = matching[0]
    assert record.levelno == logging.WARNING

    # The point of the cleanup: the caught exception's context is preserved.
    assert record.exc_info is not None, (
        "warning dropped the exception context — exc_info=True should be passed "
        "so the traceback explaining the parse failure is captured"
    )
    assert record.exc_info[0] is not None
