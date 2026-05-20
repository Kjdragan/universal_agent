"""Unit tests for the bounded outbound email tag vocabulary."""

from __future__ import annotations

import pytest

from universal_agent.services.email_tags import (
    SUBJECT_TAG_RE,
    ActionTag,
    KindTag,
    format_body_header,
    format_tagged_subject,
)

# ---------------------------------------------------------------------------
# Subject prefixing
# ---------------------------------------------------------------------------


class TestFormatTaggedSubject:
    def test_happy_path_prefixes_tag(self):
        out = format_tagged_subject(ActionTag.FYI, KindTag.DIGEST, "Daily YouTube Digest: Monday")
        assert out == "[FYI/DIGEST] Daily YouTube Digest: Monday"

    def test_accepts_string_inputs(self):
        out = format_tagged_subject("FYI", "DIGEST", "hi")
        assert out == "[FYI/DIGEST] hi"

    def test_string_inputs_case_insensitive(self):
        out = format_tagged_subject("fyi", "digest", "hi")
        assert out == "[FYI/DIGEST] hi"

    def test_idempotent_does_not_double_tag(self):
        once = format_tagged_subject(ActionTag.ACTION, KindTag.INCIDENT, "CI failed on PR #364")
        twice = format_tagged_subject(ActionTag.ACTION, KindTag.INCIDENT, once)
        thrice = format_tagged_subject(ActionTag.FYI, KindTag.DIGEST, twice)
        assert once == twice == thrice  # first tag wins, no compounding

    def test_idempotent_when_existing_tag_is_different(self):
        # Even if a caller tries to "re-tag" with different values, the
        # original tag is preserved — the first tagger wins.
        already = "[FYI/DIGEST] Daily summary"
        out = format_tagged_subject(ActionTag.ACTION, KindTag.INCIDENT, already)
        assert out == already

    def test_empty_subject(self):
        out = format_tagged_subject(ActionTag.FYI, KindTag.CRON, "")
        assert out == "[FYI/CRON]"

    def test_none_subject_normalized(self):
        out = format_tagged_subject(ActionTag.FYI, KindTag.CRON, None)  # type: ignore[arg-type]
        assert out == "[FYI/CRON]"

    def test_very_long_subject(self):
        long = "X" * 500
        out = format_tagged_subject(ActionTag.QUESTION, KindTag.PROACTIVE, long)
        assert out.startswith("[QUESTION/PROACTIVE] ")
        assert out.endswith("X" * 500)

    def test_bad_action_string_raises(self):
        with pytest.raises(ValueError):
            format_tagged_subject("URGENT", KindTag.DIGEST, "x")

    def test_bad_kind_string_raises(self):
        with pytest.raises(ValueError):
            format_tagged_subject(ActionTag.FYI, "GOSSIP", "x")

    def test_non_string_action_type_raises(self):
        with pytest.raises(TypeError):
            format_tagged_subject(42, KindTag.DIGEST, "x")  # type: ignore[arg-type]

    def test_non_string_kind_type_raises(self):
        with pytest.raises(TypeError):
            format_tagged_subject(ActionTag.FYI, 42, "x")  # type: ignore[arg-type]

    def test_regex_recognizes_all_combinations(self):
        for a in ActionTag:
            for k in KindTag:
                out = format_tagged_subject(a, k, "subject")
                assert SUBJECT_TAG_RE.match(out), f"failed to recognize: {out}"


# ---------------------------------------------------------------------------
# Body banner
# ---------------------------------------------------------------------------


class TestFormatBodyHeader:
    def test_returns_html_text_tuple(self):
        html, text = format_body_header(
            ActionTag.ACTION,
            KindTag.INCIDENT,
            source="ci-failure-watcher",
            related=["PR #364"],
            include_timestamp=False,
        )
        assert isinstance(html, str) and isinstance(text, str)

    def test_text_banner_contains_required_lines(self):
        _, text = format_body_header(
            ActionTag.ACTION,
            KindTag.INCIDENT,
            source="ci-failure-watcher",
            related=["PR #364"],
            include_timestamp=False,
        )
        assert "Tags: ACTION/INCIDENT" in text
        assert "Source: ci-failure-watcher" in text
        assert "Related: PR #364" in text
        assert text.rstrip().endswith("---")

    def test_html_banner_contains_required_fields(self):
        html, _ = format_body_header(
            ActionTag.FYI,
            KindTag.DIGEST,
            source="youtube_daily_digest cron",
            related=["day=monday"],
            include_timestamp=False,
        )
        assert "FYI/DIGEST" in html
        assert "youtube_daily_digest cron" in html
        assert "day=monday" in html
        # Should be self-contained (no script tags etc.)
        assert "<script" not in html

    def test_related_can_be_list_or_string(self):
        _, text_list = format_body_header(
            ActionTag.FYI, KindTag.DIGEST, "src", related=["a", "b"], include_timestamp=False,
        )
        _, text_str = format_body_header(
            ActionTag.FYI, KindTag.DIGEST, "src", related="a, b", include_timestamp=False,
        )
        assert "Related: a, b" in text_list
        assert "Related: a, b" in text_str

    def test_related_omitted_when_empty(self):
        _, text = format_body_header(
            ActionTag.FYI, KindTag.DIGEST, "src", related=None, include_timestamp=False,
        )
        assert "Related:" not in text

    def test_source_omitted_when_empty(self):
        _, text = format_body_header(
            ActionTag.FYI, KindTag.DIGEST, "", include_timestamp=False,
        )
        assert "Source:" not in text

    def test_timestamp_included_by_default(self):
        _, text = format_body_header(ActionTag.FYI, KindTag.CRON, "src")
        assert "Time:" in text

    def test_html_escapes_special_chars(self):
        html, _ = format_body_header(
            ActionTag.FYI,
            KindTag.DIGEST,
            source="src<script>",
            related=["a & b"],
            include_timestamp=False,
        )
        assert "<script>" not in html  # raw not present
        assert "&lt;script&gt;" in html
        assert "&amp;" in html

    def test_bad_enum_raises(self):
        with pytest.raises(ValueError):
            format_body_header("BOGUS", KindTag.DIGEST, "src")
        with pytest.raises(ValueError):
            format_body_header(ActionTag.FYI, "BOGUS", "src")


# ---------------------------------------------------------------------------
# Enum invariants — keep the vocab small and explicit.
# ---------------------------------------------------------------------------


def test_action_tag_has_exactly_four_values():
    assert {a.value for a in ActionTag} == {"FYI", "ACTION", "DECISION", "QUESTION"}


def test_kind_tag_has_exactly_seven_values():
    assert {k.value for k in KindTag} == {
        "DIGEST",
        "TUTORIAL",
        "PROACTIVE",
        "INCIDENT",
        "CRON",
        "SYSTEM",
        "DEPLOY",
    }
