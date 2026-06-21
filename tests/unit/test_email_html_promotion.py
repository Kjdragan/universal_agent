"""Regression test for the empty-HTML-part email bug.

VP [VP Status]/failure emails (and the deterministic VP-stream forward) pass
only a plaintext body. The toolkit bridge always POSTs an "html" field to
AgentMail, and AgentMail renders an empty html value as a blank
<div dir=ltr></div> — so the message lands blank in Gmail/Outlook.

These assert that the shared promotion helper turns a text-only body into a
non-empty, HTML-escaped html part with <br> line breaks, and that empty text
yields an empty string (so we never emit a stray <div></div>).
"""

from universal_agent.services.email_tags import promote_text_to_html


def test_promote_text_yields_nonempty_html_with_breaks():
    html = promote_text_to_html("line1\nline2")
    assert html, "text-only body must produce a non-empty html part"
    assert "line1" in html and "line2" in html
    # Newline becomes a <br> so the body renders identically to plaintext.
    assert "<br>" in html
    # Wrapped in a div so AgentMail does not emit a blank container.
    assert html.startswith("<div>") and html.endswith("</div>")


def test_promote_text_escapes_html_metacharacters():
    html = promote_text_to_html("a < b & c > d")
    assert "&lt;" in html and "&amp;" in html and "&gt;" in html
    # The raw angle bracket from the body must not survive unescaped.
    assert "< b" not in html


def test_promote_empty_text_yields_empty_string():
    assert promote_text_to_html("") == ""
    assert promote_text_to_html("   \n  ") == ""
