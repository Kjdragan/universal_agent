"""Unit tests for services/transcript_corpus.py — hermetic, no network."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile

import pytest

from universal_agent.services.transcript_corpus import (
    get_persisted_transcript,
    load_full_sources_for_candidate,
    resolve_csi_db_path,
)


def _make_db(path: str) -> None:
    """Create a minimal youtube_transcripts table in the given DB file."""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS youtube_transcripts (
            video_id         TEXT PRIMARY KEY,
            event_id         TEXT,
            channel_id       TEXT,
            channel_name     TEXT,
            title            TEXT,
            published_at     TEXT,
            language         TEXT,
            char_count       INTEGER NOT NULL DEFAULT 0,
            transcript_text  TEXT NOT NULL,
            source_ref       TEXT,
            fetched_at       TEXT DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute(
        "INSERT INTO youtube_transcripts "
        "(video_id, event_id, channel_id, channel_name, title, published_at, "
        "char_count, transcript_text, source_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "known_vid", "evt_001", "chan_001", "Test Channel", "Test Title",
            "2026-06-11T00:00:00Z", 42, "the full transcript text", "ua@127.0.0.1",
        ),
    )
    conn.commit()
    conn.close()


class TestGetPersistedTranscript:
    def test_returns_text_for_known_video(self, tmp_path):
        db = str(tmp_path / "csi.db")
        _make_db(db)
        result = get_persisted_transcript("known_vid", csi_db_path=db)
        assert result == "the full transcript text"

    def test_returns_none_for_unknown_video(self, tmp_path):
        db = str(tmp_path / "csi.db")
        _make_db(db)
        result = get_persisted_transcript("nonexistent_vid", csi_db_path=db)
        assert result is None

    def test_returns_none_for_nonexistent_db(self, tmp_path):
        missing = str(tmp_path / "no_such_file.db")
        result = get_persisted_transcript("any_vid", csi_db_path=missing)
        assert result is None

    def test_returns_none_when_table_missing(self, tmp_path):
        db = str(tmp_path / "empty.db")
        # Create DB with no tables at all.
        conn = sqlite3.connect(db)
        conn.close()
        result = get_persisted_transcript("any_vid", csi_db_path=db)
        assert result is None


class TestLoadFullSourcesForCandidate:
    def test_attaches_transcript_for_known_video(self, tmp_path):
        db = str(tmp_path / "csi.db")
        _make_db(db)

        original = [{"video_id": "known_vid", "key_claims": ["claim A", "claim B"]}]
        enriched = load_full_sources_for_candidate(
            original, csi_db_path=db, allow_refetch=False
        )

        assert len(enriched) == 1
        assert enriched[0]["full_transcript"] == "the full transcript text"
        # Original keys preserved.
        assert enriched[0]["key_claims"] == ["claim A", "claim B"]
        assert enriched[0]["video_id"] == "known_vid"

    def test_returns_none_transcript_for_unknown_video(self, tmp_path):
        db = str(tmp_path / "csi.db")
        _make_db(db)

        original = [{"video_id": "unknown_vid", "key_claims": ["claim X"]}]
        enriched = load_full_sources_for_candidate(
            original, csi_db_path=db, allow_refetch=False
        )

        assert len(enriched) == 1
        assert enriched[0]["full_transcript"] is None
        # key_claims still present.
        assert enriched[0]["key_claims"] == ["claim X"]

    def test_does_not_mutate_inputs(self, tmp_path):
        db = str(tmp_path / "csi.db")
        _make_db(db)

        original = [{"video_id": "known_vid", "key_claims": ["c1"]}]
        original_copy = [dict(sig) for sig in original]

        load_full_sources_for_candidate(original, csi_db_path=db, allow_refetch=False)

        # Input list and its dicts must be unchanged.
        assert original == original_copy
        assert "full_transcript" not in original[0]

    def test_handles_mixed_known_and_unknown(self, tmp_path):
        db = str(tmp_path / "csi.db")
        _make_db(db)

        sigs = [
            {"video_id": "known_vid", "key_claims": ["k"]},
            {"video_id": "no_such_vid", "key_claims": ["n"]},
        ]
        enriched = load_full_sources_for_candidate(sigs, csi_db_path=db, allow_refetch=False)

        assert len(enriched) == 2
        assert enriched[0]["full_transcript"] == "the full transcript text"
        assert enriched[1]["full_transcript"] is None

    def test_handles_empty_video_id(self, tmp_path):
        db = str(tmp_path / "csi.db")
        _make_db(db)

        sigs = [{"video_id": "", "key_claims": ["x"]}]
        enriched = load_full_sources_for_candidate(sigs, csi_db_path=db, allow_refetch=False)

        assert len(enriched) == 1
        assert enriched[0]["full_transcript"] is None
