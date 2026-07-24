"""Error-path logging in the CSI watchlist router must preserve context.

The router's 500 handlers used vague f-string messages
(e.g. ``"Error reading watchlist: {e}"``) that dropped both the exception
traceback and the entity being acted on (channel id / category name / file
path). When a watchlist endpoint fails, the operator must be able to tell
*which* entity failed and *why* from the log line alone.

These tests force two error paths (a corrupt-watchlist read and a
write-protected mutation) and assert the emitted ERROR log record carries the
entity context and the exception traceback (``exc_info``).
"""

import json
import logging
import os

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from universal_agent.api.routers import csi_watchlist
from universal_agent.api.routers.csi_watchlist import router as watchlist_router
import universal_agent.gateway_server as gs

LOGGER_NAME = "universal_agent.api.routers.csi_watchlist"


def _client(monkeypatch) -> TestClient:
    monkeypatch.setattr(gs, "OPS_TOKEN", "test-ops-token", raising=False)
    monkeypatch.setattr(gs, "OPS_JWT_SECRET", "", raising=False)
    app = FastAPI()
    app.include_router(
        watchlist_router, dependencies=[Depends(gs._require_ops_auth)]
    )
    return TestClient(app, raise_server_exceptions=False)


def _error_records(caplog):
    return [
        r
        for r in caplog.records
        if r.name == LOGGER_NAME and r.levelno == logging.ERROR
    ]


def test_get_watchlist_log_includes_path_and_traceback(monkeypatch, tmp_path, caplog):
    """A corrupt watchlist file must yield a 500 whose log line names the
    failing file path and preserves the exception traceback."""
    path = tmp_path / "watchlist.json"
    path.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(csi_watchlist, "get_watchlist_path", lambda: path)

    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)
    client = _client(monkeypatch)
    resp = client.get(
        "/api/v1/csi/watchlist", headers={"x-ua-ops-token": "test-ops-token"}
    )

    assert resp.status_code == 500
    errs = _error_records(caplog)
    assert errs, "expected an ERROR log record on the read failure"
    rec = errs[-1]
    assert str(path) in rec.getMessage(), "log message must name the failing path"
    assert rec.exc_info is not None and rec.exc_info[1] is not None, (
        "traceback must be preserved (logger.exception / exc_info=True)"
    )


def test_add_category_log_includes_name_and_traceback(monkeypatch, tmp_path, caplog):
    """A failed category write must yield a 500 whose log line names the
    requested category and preserves the exception traceback."""
    path = tmp_path / "watchlist.json"
    path.write_text(
        json.dumps({"channels": [], "categories": []}), encoding="utf-8"
    )
    os.chmod(path, 0o444)  # read-only: the read succeeds, the write raises
    monkeypatch.setattr(csi_watchlist, "get_watchlist_path", lambda: path)

    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)
    client = _client(monkeypatch)
    try:
        resp = client.post(
            "/api/v1/csi/watchlist/categories",
            headers={"x-ua-ops-token": "test-ops-token"},
            json={"name": "test_category_xyz"},
        )
    finally:
        os.chmod(path, 0o600)  # restore so pytest can clean up tmp_path

    assert resp.status_code == 500
    errs = _error_records(caplog)
    assert errs, "expected an ERROR log record on the write failure"
    rec = errs[-1]
    assert "test_category_xyz" in rec.getMessage(), (
        "log message must name the category being added"
    )
    assert rec.exc_info is not None and rec.exc_info[1] is not None, (
        "traceback must be preserved (logger.exception / exc_info=True)"
    )
