"""Regression: cron self-send email delivery must be tracked.

Reproduces the ``paper_to_podcast_daily`` resume/self-send defect: the agent
delivers the podcast directly via an AgentMail tool (the local attachment
tool for the mp3, or ``mcp__AgentMail__send_message``), bypassing
``cron_artifact_notifier``. Without the delivery-tracking layer no row landed
in ``proactive_artifact_emails`` and the subject lacked the ``[<job_id>]``
tag the ``paper_to_podcast_email_delivery`` proactive-health watchdog keys on
(``recipient='kevinjdragan@gmail.com' AND subject LIKE '[<job_id>]%'``),
producing a recurring false 'no email in 30h' *critical* even though Kevin
actually received the podcast (verified Amazon SES ``message_id``).

These tests pin the shared layer
``cron_artifact_notifier.record_cron_run_delivery_email`` that the
self-send observation sites (``local_toolkit_bridge`` + ``hooks``) call.
"""

from __future__ import annotations

import json
import sqlite3

from universal_agent.services import proactive_artifacts
from universal_agent.services.cron_artifact_notifier import (
    record_cron_run_delivery_email,
)

# The real paper_to_podcast cron job id (proactive_pipeline_invariants.PAPER_TO_PODCAST_JOB_ID)
# and the recipient the watchdog probes.
JOB_ID = "2afe05ab96"
RECIPIENT = "kevinjdragan@gmail.com"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    proactive_artifacts.ensure_schema(conn)
    return conn


def _watchdog_last_sent(conn: sqlite3.Connection) -> tuple[int, str]:
    """The exact query the proactive-health watchdog runs."""
    row = conn.execute(
        "SELECT MAX(sent_at) AS last_sent, COUNT(*) AS total "
        "FROM proactive_artifact_emails "
        "WHERE recipient = ? AND subject LIKE ?",
        (RECIPIENT, f"[{JOB_ID}]%"),
    ).fetchone()
    return int(row["total"] or 0), str(row["last_sent"] or "")


def test_self_send_lands_tagged_row_for_watchdog():
    """Core regression: an untagged self-send records a [<job_id>]-tagged row."""
    conn = _connect()
    untagged = "Causal inference & causal ML: Top 5 Papers + Podcast + Quiz (resumed run)"

    record_cron_run_delivery_email(
        conn,
        job_id=JOB_ID,
        message_id="ses-abc-001",
        subject=untagged,
        recipient=RECIPIENT,
        source="agentmail_send_with_local_attachments",
    )

    total, last_sent = _watchdog_last_sent(conn)
    assert total >= 1, "watchdog query found no tagged delivery row"
    assert last_sent, "sent_at must be populated"

    row = conn.execute(
        "SELECT subject, recipient, message_id, metadata_json FROM proactive_artifact_emails "
        "WHERE message_id = 'ses-abc-001'"
    ).fetchone()
    assert row["subject"].startswith(f"[{JOB_ID}]"), row["subject"]
    assert row["recipient"] == RECIPIENT
    # The original (untagged) subject is preserved in metadata, nothing lost.
    assert json.loads(row["metadata_json"])["original_subject"] == untagged
    conn.close()


def test_idempotent_on_message_id():
    """Repeated calls with the same message_id never duplicate the row."""
    conn = _connect()
    for _ in range(3):
        record_cron_run_delivery_email(
            conn,
            job_id=JOB_ID,
            message_id="ses-dup-001",
            subject="today's podcast",
            recipient=RECIPIENT,
        )
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM proactive_artifact_emails WHERE message_id = 'ses-dup-001'"
    ).fetchone()["c"]
    assert count == 1
    conn.close()


def test_dedup_against_notifier_message_id():
    """If the notifier already recorded the same message_id, the self-send layer skips."""
    conn = _connect()
    notifier_artifact = proactive_artifacts.upsert_artifact(
        conn,
        artifact_type="cron_run_output",
        source_kind="cron_artifact",
        source_ref=f"{JOB_ID}:notifier",
        title="notifier disclosure",
        status=proactive_artifacts.ARTIFACT_STATUS_SURFACED,
        delivery_state=proactive_artifacts.DELIVERY_EMAILED,
        metadata={"job_id": JOB_ID},
    )
    proactive_artifacts.record_email_delivery(
        conn,
        artifact_id=notifier_artifact["artifact_id"],
        message_id="ses-shared-001",
        subject=f"[{JOB_ID}] notifier disclosure",
        recipient=RECIPIENT,
    )
    before = conn.execute("SELECT COUNT(*) AS c FROM proactive_artifact_emails").fetchone()["c"]

    record_cron_run_delivery_email(
        conn,
        job_id=JOB_ID,
        message_id="ses-shared-001",  # same message_id the notifier recorded
        subject="self send body",
        recipient=RECIPIENT,
    )
    after = conn.execute("SELECT COUNT(*) AS c FROM proactive_artifact_emails").fetchone()["c"]
    assert before == after, "self-send layer must not duplicate a notifier-recorded delivery"
    conn.close()


def test_coalesces_onto_existing_cron_artifact():
    """A self-send attaches to an existing same-cron open artifact, minting no new row."""
    conn = _connect()
    existing = proactive_artifacts.upsert_artifact(
        conn,
        artifact_type="cron_run_output",
        source_kind="cron_artifact",
        source_ref=f"{JOB_ID}:prior",
        title="prior run artifact",
        status=proactive_artifacts.ARTIFACT_STATUS_PRODUCED,
        delivery_state=proactive_artifacts.DELIVERY_NOT_SURFACED,
        metadata={"job_id": JOB_ID, "task_id": f"cron:{JOB_ID}"},
    )
    existing_id = existing["artifact_id"]

    record_cron_run_delivery_email(
        conn,
        job_id=JOB_ID,
        message_id="ses-coalesce-001",
        subject="podcast",
        recipient=RECIPIENT,
    )

    n_artifacts = conn.execute(
        "SELECT COUNT(*) AS c FROM proactive_artifacts WHERE source_kind = 'cron_artifact'"
    ).fetchone()["c"]
    assert n_artifacts == 1, "self-send should coalesce, not mint a second artifact"
    email = conn.execute(
        "SELECT artifact_id FROM proactive_artifact_emails WHERE message_id = 'ses-coalesce-001'"
    ).fetchone()
    assert email["artifact_id"] == existing_id
    conn.close()


def test_requires_job_id_and_message_id():
    """No job_id or no message_id -> no-op (guards against spurious rows)."""
    conn = _connect()
    record_cron_run_delivery_email(conn, job_id="", message_id="x", subject="s", recipient=RECIPIENT)
    record_cron_run_delivery_email(conn, job_id=JOB_ID, message_id="", subject="s", recipient=RECIPIENT)
    assert conn.execute("SELECT COUNT(*) AS c FROM proactive_artifact_emails").fetchone()["c"] == 0
    conn.close()


def test_never_raises_on_bad_db():
    """The layer is best-effort: a broken conn must not propagate into the send path."""
    # A closed connection makes every query raise (ProgrammingError); the
    # helper's ``ensure_schema`` can't paper over this, so the try/except must
    # swallow it and return None rather than disrupting the email send path.
    conn = sqlite3.connect(":memory:")
    conn.close()
    result = record_cron_run_delivery_email(
        conn,
        job_id=JOB_ID,
        message_id="ses-bad-001",
        subject="podcast",
        recipient=RECIPIENT,
    )
    assert result is None


# ── Runtime-path regressions (second-pass fix for #1196) ─────────────────
# The tests above pin the shared ``record_cron_run_delivery_email`` layer with
# direct calls. The 2026-06-26 false critical slipped past because no test
# exercised the REAL path: a cron-context ``agentmail_send_with_local_attachments``
# send flowing through the tool impl → ``_record_agentmail_delivery_from_runtime``
# → ``resolve_email_tracking_from_runtime``. That path silently no-op'd because
# ``InProcessGateway.run_query`` (the cron LLM execution path) never bound
# ``request_runtime``, so ``get_request_runtime()`` returned None inside the
# in-process tool (conn is None → skip). These two tests close that gap end to end.


def test_self_send_via_runtime_records_tagged_row(tmp_path, monkeypatch):
    """A cron-context ``agentmail_send_with_local_attachments`` send must land a
    watchdog-tagged ``proactive_artifact_emails`` row through the REAL tool-impl
    runtime path — not a direct ``record_cron_run_delivery_email`` call.

    Reproduces the 2026-06-26 shape: the podcast email sent fine (real SES
    message_id) but no delivery row landed, so the
    ``paper_to_podcast_email_delivery`` watchdog fired a false 'no email in 30h'
    critical. Before the gateway fix, ``get_request_runtime()`` was None inside
    the in-process tool, so the recorder's ``conn is None`` guard skipped it."""
    import asyncio
    import urllib.request as urllib_request

    from universal_agent.request_runtime import (
        RequestRuntimeContext,
        reset_request_runtime,
        set_request_runtime,
    )
    from universal_agent.tools.local_toolkit_bridge import (
        _agentmail_send_with_local_attachments_impl,
    )

    # The recorder opens its own connection via get_activity_db_path(); redirect
    # it to a temp file the test reads back.
    db_path = tmp_path / "activity_state.db"
    monkeypatch.setenv("UA_ACTIVITY_DB_PATH", str(db_path))
    seed = sqlite3.connect(str(db_path))
    seed.row_factory = sqlite3.Row
    proactive_artifacts.ensure_schema(seed)
    seed.close()

    # Mock the AgentMail HTTP send so the test never touches the network.
    class _FakeResp:
        def __init__(self, body):
            self._body = body.encode("utf-8")

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(req, data=None, timeout=None):
        return _FakeResp(json.dumps({"message_id": "ses-cron-runtime-001"}))

    monkeypatch.setattr(urllib_request, "urlopen", _fake_urlopen)
    monkeypatch.setenv("AGENTMAIL_API_KEY", "test-key")

    # Bind the cron runtime context exactly as the fixed
    # InProcessGateway.run_query does. metadata["job_id"] is what the recorder
    # reads to tag the subject for the watchdog.
    token = set_request_runtime(
        RequestRuntimeContext(
            session_id="sess-cron-runtime",
            workspace_dir=str(tmp_path),
            source="cron",
            run_kind="cron",
            metadata={"source": "cron", "job_id": JOB_ID, "run_kind": "cron"},
        )
    )
    try:
        result = asyncio.run(
            _agentmail_send_with_local_attachments_impl(
                {
                    "inboxId": "oddcity216@agentmail.to",
                    "to": [RECIPIENT],
                    "subject": "Daily paper podcast (resumed run)",
                    "text": "podcast attached",
                    "html": "",
                    "attachment_paths": [],
                }
            )
        )
    finally:
        reset_request_runtime(token)

    # The send itself succeeded and surfaced the message_id.
    assert "ses-cron-runtime-001" in json.dumps(result)

    # The watchdog query must now find the tagged row.
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        total, _last_sent = _watchdog_last_sent(conn)
        assert total >= 1, "cron-context self-send did not land a watchdog-tagged row"
        row = conn.execute(
            "SELECT subject, recipient, message_id FROM proactive_artifact_emails "
            "WHERE message_id = 'ses-cron-runtime-001'"
        ).fetchone()
        assert row is not None, "delivery row missing for the cron self-send"
        assert row["subject"].startswith(f"[{JOB_ID}]"), row["subject"]
        assert row["recipient"] == RECIPIENT
    finally:
        conn.close()


def test_inprocess_gateway_binds_cron_request_runtime(tmp_path):
    """Pins the root-cause fix directly: ``InProcessGateway.run_query`` (the cron
    LLM execution path) must bind ``request_runtime`` so in-process internal MCP
    tools see ``run_kind='cron'`` and ``metadata['job_id']``. Before the fix this
    binding existed only on the interactive chat path (``gateway_server._run``),
    so ``get_request_runtime()`` returned None on every cron run and the
    self-send recorder silently skipped."""
    import asyncio

    from universal_agent.gateway import GatewayRequest, GatewaySession, InProcessGateway
    from universal_agent.request_runtime import get_request_runtime

    captured: dict = {}

    class _CapturingGateway(InProcessGateway):
        async def execute(self, session, request):
            rt = get_request_runtime()
            captured["bound"] = rt is not None
            captured["run_kind"] = getattr(rt, "run_kind", None)
            captured["source"] = getattr(rt, "source", None)
            captured["job_id"] = (rt.metadata or {}).get("job_id") if rt else None
            if False:
                yield  # force async-generator semantics; yield nothing

    # Bypass the heavy InProcessGateway.__init__ (DB connections / reaper) —
    # run_query only touches self.execute / self._use_legacy / self._bridge.
    gw = _CapturingGateway.__new__(_CapturingGateway)
    gw._use_legacy = False
    gw._bridge = None
    gw._hooks = None

    session = GatewaySession(
        session_id="sess-cron-gw",
        user_id=f"cron:{JOB_ID}",
        workspace_dir=str(tmp_path),
        metadata={"run_kind": "cron", "job_id": JOB_ID, "source": "cron"},
    )
    request = GatewayRequest(
        user_input="run paper_to_podcast",
        metadata={"source": "cron", "job_id": JOB_ID, "run_kind": "cron"},
    )

    asyncio.run(gw.run_query(session, request))

    assert captured.get("bound") is True, (
        "request_runtime was not bound on the cron (InProcessGateway) path"
    )
    assert captured.get("run_kind") == "cron"
    assert captured.get("source") == "cron"
    assert captured.get("job_id") == JOB_ID
