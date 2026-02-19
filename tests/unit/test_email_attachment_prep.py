from __future__ import annotations

from pathlib import Path

from universal_agent.utils.email_attachment_prep import (
    prepare_attachment_for_composio_upload,
)


def test_prepare_attachment_renders_markdown_for_gmail(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("UA_GMAIL_RENDER_MARKDOWN_ATTACHMENTS", raising=False)
    md_path = tmp_path / "report.md"
    md_path.write_text("# Title\n\n- one\n- two\n", encoding="utf-8")

    prepared, meta = prepare_attachment_for_composio_upload(
        str(md_path),
        tool_slug="GMAIL_SEND_EMAIL",
        toolkit_slug="gmail",
    )

    prepared_path = Path(prepared)
    assert prepared_path.exists()
    assert prepared_path.suffix == ".html"
    assert prepared_path.name.endswith("_rendered_for_email.html")
    assert meta.get("rendered_from_markdown") == "true"
    assert meta.get("original_local_path") == str(md_path.resolve())
    html_text = prepared_path.read_text(encoding="utf-8")
    assert "<html" in html_text.lower()
    assert "Title" in html_text


def test_prepare_attachment_keeps_non_markdown_files(tmp_path: Path):
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    prepared, meta = prepare_attachment_for_composio_upload(
        str(pdf_path),
        tool_slug="GMAIL_SEND_EMAIL",
        toolkit_slug="gmail",
    )

    assert Path(prepared) == pdf_path.resolve()
    assert meta == {}


def test_prepare_attachment_can_disable_markdown_render(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_GMAIL_RENDER_MARKDOWN_ATTACHMENTS", "0")
    md_path = tmp_path / "raw.md"
    md_path.write_text("plain markdown", encoding="utf-8")

    prepared, meta = prepare_attachment_for_composio_upload(
        str(md_path),
        tool_slug="GMAIL_SEND_EMAIL",
        toolkit_slug="gmail",
    )

    assert Path(prepared) == md_path.resolve()
    assert meta == {}
