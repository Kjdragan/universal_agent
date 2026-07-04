"""Regression guard: ``work_threads._load_payload`` must not silently swallow
JSON/IO failures and return an empty default with zero observability.

Theme: "improve error messages and logging context in except blocks." A
corrupted ``work_threads.json`` previously looked identical to "no threads"
(the loader returns ``{"threads": []}`` from its bare ``except Exception``),
so state could effectively vanish with no log trail. The fix emits a
``logger.warning`` naming the path and preserving the traceback
(``exc_info=True`` — house idiom, see ``test_hooks_service_logging_context``)
before returning the safe default.
"""

import logging

from universal_agent.work_threads import list_work_threads


def test_corrupt_state_file_logs_warning_with_context(tmp_path, monkeypatch, caplog):
    state = tmp_path / "work_threads.json"
    state.write_text("NOT JSON{{{")
    monkeypatch.setenv("UA_WORK_THREADS_PATH", str(state))

    with caplog.at_level(logging.WARNING, logger="universal_agent.work_threads"):
        result = list_work_threads()

    # Behaviour unchanged: parse failure -> safe empty default.
    assert result == []

    matching = [
        record for record in caplog.records
        if "work threads state" in record.getMessage()
    ]
    assert matching, "expected a warning naming the work threads state file"
    record = matching[0]
    assert record.levelno == logging.WARNING
    assert str(state) in record.getMessage()
    assert record.exc_info is not None, (
        "warning dropped the exception context — exc_info=True should be "
        "passed so the traceback explaining the parse failure is captured"
    )
