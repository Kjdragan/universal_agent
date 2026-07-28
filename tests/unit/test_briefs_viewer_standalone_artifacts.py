"""briefs_viewer_get must serve full-document artifacts standalone.

Authored briefs are complete dark-themed HTML documents. Wrapping one in
the light ``_brief_chrome`` keeps its <style> block but replaces its dark
body background, leaving pale text on white (unreadable). Full documents
are served as-is with the feedback widget injected before ``</body>``;
fragments keep the chrome wrap.
"""

from __future__ import annotations

from universal_agent import gateway_server
from universal_agent.services import cron_artifact_notifier, proactive_artifacts


def _setup(monkeypatch, tmp_path, html: str) -> None:
    artifact_file = tmp_path / "brief.html"
    artifact_file.write_text(html, encoding="utf-8")
    artifact = {
        "artifact_id": "pa_test",
        "artifact_path": str(artifact_file),
        "title": "Test brief",
        "summary": "s",
    }

    class _Conn:
        def close(self) -> None:
            pass

    monkeypatch.setattr(gateway_server, "_activity_connect", lambda: _Conn())
    monkeypatch.setattr(proactive_artifacts, "get_artifact", lambda conn, aid: artifact)
    monkeypatch.setattr(
        cron_artifact_notifier, "sign_feedback_token", lambda aid, vote: "tok"
    )
    monkeypatch.setattr(
        gateway_server, "_brief_feedback_base_url", lambda: "https://example.test"
    )


def test_full_document_served_standalone(monkeypatch, tmp_path):
    doc = (
        "<!DOCTYPE html>\n<html><head><style>body{background:#0d1117}</style>"
        "</head><body><p>dark brief</p></body></html>"
    )
    _setup(monkeypatch, tmp_path, doc)
    resp = gateway_server.briefs_viewer_get("pa_test")
    body = resp.body.decode("utf-8")
    assert body.lstrip().lower().startswith("<!doctype")
    assert "background:#ffffff" not in body  # no light chrome wrap
    assert body.count("<!DOCTYPE") + body.count("<!doctype") == 1
    # feedback widget injected inside the document body
    assert body.index("Rate this brief") < body.index("</body>")


def test_fragment_still_wrapped_in_chrome(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, "<p>fragment brief</p>")
    resp = gateway_server.briefs_viewer_get("pa_test")
    body = resp.body.decode("utf-8")
    assert "background:#ffffff" in body
    assert "fragment brief" in body
    assert "Rate this brief" in body
