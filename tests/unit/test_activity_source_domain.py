"""Tests for _activity_source_domain routing table refactor."""

from universal_agent.gateway_server import _activity_source_domain


class TestPrefixRouting:
    """Each prefix in the routing table maps to the correct domain."""

    def test_csi(self):
        assert _activity_source_domain("csi_daily_report") == "csi"

    def test_csi_exact(self):
        assert _activity_source_domain("csi") == "csi"

    def test_youtube(self):
        assert _activity_source_domain("youtube_ingest") == "tutorial"

    def test_youtube_exact(self):
        assert _activity_source_domain("youtube") == "tutorial"

    def test_cron(self):
        assert _activity_source_domain("cron_nightly_sync") == "cron"

    def test_cron_exact(self):
        assert _activity_source_domain("cron") == "cron"

    def test_heartbeat(self):
        assert _activity_source_domain("heartbeat_check") == "heartbeat"

    def test_heartbeat_exact(self):
        assert _activity_source_domain("heartbeat") == "heartbeat"

    def test_continuity(self):
        assert _activity_source_domain("continuity_resume") == "continuity"

    def test_continuity_exact(self):
        assert _activity_source_domain("continuity") == "continuity"

    def test_system(self):
        assert _activity_source_domain("system_maintenance") == "system"

    def test_system_exact(self):
        assert _activity_source_domain("system") == "system"

    def test_agentmail(self):
        assert _activity_source_domain("agentmail_incoming") == "simone"

    def test_agentmail_exact(self):
        assert _activity_source_domain("agentmail") == "simone"

    def test_autonomous(self):
        assert _activity_source_domain("autonomous_research") == "cron"

    def test_autonomous_heartbeat_returns_heartbeat(self):
        """autonomous_heartbeat must resolve to heartbeat, NOT cron."""
        assert _activity_source_domain("autonomous_heartbeat_run") == "heartbeat"

    def test_autonomous_heartbeat_exact(self):
        assert _activity_source_domain("autonomous_heartbeat") == "heartbeat"


class TestSubstringTutorialMatch:
    """The word tutorial anywhere in the kind string yields tutorial."""

    def test_video_tutorial(self):
        assert _activity_source_domain("video_tutorial") == "tutorial"

    def test_tutorial_build(self):
        assert _activity_source_domain("tutorial_build") == "tutorial"

    def test_my_tutorial_run(self):
        assert _activity_source_domain("my_tutorial_run") == "tutorial"

    def test_tutorial_midstring(self):
        assert _activity_source_domain("some_tutorial_thing") == "tutorial"


class TestMetadataOverrides:
    """Metadata-based routing: source=heartbeat overrides all; pipeline=csi_ is a fallback."""

    def test_metadata_source_heartbeat(self):
        """metadata source=heartbeat overrides any kind."""
        assert _activity_source_domain("random_kind", {"source": "heartbeat"}) == "heartbeat"

    def test_metadata_source_heartbeat_whitespace(self):
        assert _activity_source_domain("random_kind", {"source": "  heartbeat  "}) == "heartbeat"

    def test_metadata_source_heartbeat_case_insensitive(self):
        assert _activity_source_domain("random_kind", {"source": "Heartbeat"}) == "heartbeat"

    def test_metadata_source_heartbeat_overrides_csi(self):
        """Even a csi kind should be overridden by metadata source=heartbeat."""
        assert _activity_source_domain("csi_report", {"source": "heartbeat"}) == "heartbeat"

    def test_metadata_pipeline_csi(self):
        """metadata pipeline starting with csi_ returns csi."""
        assert _activity_source_domain("random_kind", {"pipeline": "csi_daily"}) == "csi"

    def test_metadata_pipeline_csi_with_prefix(self):
        assert _activity_source_domain("random_kind", {"pipeline": "csi_ingest_hourly"}) == "csi"

    def test_metadata_pipeline_csi_fallback_for_unmatched_kind(self):
        """pipeline csi_ is a fallback when kind has no prefix match."""
        assert _activity_source_domain("random_kind", {"pipeline": "csi_report"}) == "csi"

    def test_kind_prefix_takes_precedence_over_pipeline(self):
        """When kind matches a prefix, it wins over pipeline metadata.

        This preserves original fallback-only semantics: pipeline=csi_ only
        fires when no kind prefix matched.  Without this, agentmail events
        with pipeline=csi_daily would be misrouted to 'csi' instead of 'simone'.
        """
        assert _activity_source_domain("agentmail_incoming", {"pipeline": "csi_daily"}) == "simone"
        assert _activity_source_domain("autonomous_research", {"pipeline": "csi_daily"}) == "cron"
        assert _activity_source_domain("system_check", {"pipeline": "csi_report"}) == "system"

    def test_metadata_source_takes_precedence_over_pipeline(self):
        """source=heartbeat should be checked before pipeline=csi_."""
        assert (
            _activity_source_domain(
                "random_kind",
                {"source": "heartbeat", "pipeline": "csi_daily"},
            )
            == "heartbeat"
        )


class TestEdgeCases:
    """Edge cases: empty, None, case normalization, unknown kinds."""

    def test_empty_kind(self):
        assert _activity_source_domain("") == "system"

    def test_none_kind(self):
        assert _activity_source_domain(None) == "system"

    def test_none_metadata(self):
        assert _activity_source_domain("csi_report", None) == "csi"

    def test_case_insensitive_kind(self):
        assert _activity_source_domain("CSI_Report") == "csi"

    def test_uppercase_youtube(self):
        assert _activity_source_domain("YouTube_Ingest") == "tutorial"

    def test_whitespace_kind(self):
        assert _activity_source_domain("  csi_report  ") == "csi"

    def test_unknown_kind_returns_system(self):
        assert _activity_source_domain("unknown_kind") == "system"

    def test_totally_random_string(self):
        assert _activity_source_domain("xyzzy_plugh") == "system"

    def test_metadata_empty_dict(self):
        assert _activity_source_domain("heartbeat", {}) == "heartbeat"

    def test_metadata_source_empty_string(self):
        assert _activity_source_domain("csi_report", {"source": ""}) == "csi"

    def test_metadata_pipeline_wrong_prefix(self):
        assert _activity_source_domain("heartbeat_check", {"pipeline": "other_thing"}) == "heartbeat"
