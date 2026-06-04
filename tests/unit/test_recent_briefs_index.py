"""Tests for the recent briefs index helper (PR B insight pipeline)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from universal_agent.services import (
    proactive_artifacts,
    proactive_convergence,
    recent_briefs_index,
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    proactive_artifacts.ensure_schema(conn)
    proactive_convergence.ensure_schema(conn)
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_ship_artifact(
    conn: sqlite3.Connection,
    *,
    artifact_id: str,
    title: str,
    thesis: str,
    candidate_id: str,
    key_entities: list[str],
    ship_reasoning: str,
    updated_at: str | None = None,
) -> str:
    ts = updated_at or _now_iso()
    meta = {
        "candidate_id": candidate_id,
        "thesis": thesis,
        "key_entities": key_entities,
        "ship_reasoning": ship_reasoning,
        "decided_at": ts,
    }
    artifact = proactive_artifacts.upsert_artifact(
        conn,
        artifact_id=artifact_id,
        artifact_type="intel_brief",
        source_kind="convergence_candidate",
        source_ref=candidate_id,
        title=title,
        summary=thesis,
        metadata=meta,
    )
    # Set verdict + verdict_reasoning + updated_at directly.
    conn.execute(
        "UPDATE proactive_artifacts SET verdict = 'ship', verdict_reasoning = ?, "
        "updated_at = ? WHERE artifact_id = ?",
        (ship_reasoning, ts, artifact["artifact_id"]),
    )
    conn.commit()
    return artifact["artifact_id"]


def _insert_skip_defer_candidate(
    conn: sqlite3.Connection,
    *,
    candidate_id: str,
    verdict: str,
    primary_topics: list[str],
    channel_names: list[str],
    reasoning: str,
    evaluated_at: str | None = None,
) -> None:
    ts = evaluated_at or _now_iso()
    conn.execute(
        """
        INSERT INTO convergence_candidates (
            candidate_id, video_ids_json, channel_names_json, channel_count,
            primary_topics_json, signatures_json, task_id, verdict,
            verdict_reasoning, artifact_id, detected_at, evaluated_at,
            created_at, updated_at, metadata_json
        ) VALUES (?, '[]', ?, ?, ?, '[]', '', ?, ?, '', ?, ?, ?, ?, '{}')
        """,
        (
            candidate_id,
            json.dumps(channel_names),
            len(channel_names),
            json.dumps(primary_topics),
            verdict,
            reasoning,
            ts,
            ts,
            ts,
            ts,
        ),
    )
    conn.commit()


class TestBuildIndex:
    def test_empty_db_renders_header_and_placeholder(self):
        conn = _make_conn()
        text = recent_briefs_index.build_recent_briefs_index(conn)
        assert "# Recent Intel Briefs Index" in text
        assert "Lookback: 48 hours" in text
        assert "No verdicts" in text

    def test_ship_block_appears(self):
        conn = _make_conn()
        _insert_ship_artifact(
            conn,
            artifact_id="pa_test0000aaaa0001",
            title="GLM 4.6 emergence",
            thesis="Three independent channels analyzed GLM 4.6 within 36 hours.",
            candidate_id="cand_aaaabbbbccccdddd",
            key_entities=["GLM 4.6", "ZAI", "AnyCoder"],
            ship_reasoning="Strong cross-channel diversity; novel framing.",
        )
        text = recent_briefs_index.build_recent_briefs_index(conn)
        assert "## [SHIP] GLM 4.6 emergence" in text
        assert "candidate_id: cand_aaaabbbbccccdddd" in text
        assert "artifact_id: pa_test0000aaaa0001" in text
        assert "GLM 4.6" in text
        assert "operator_rating: null" in text

    def test_skip_and_defer_blocks_render(self):
        conn = _make_conn()
        _insert_skip_defer_candidate(
            conn,
            candidate_id="cand_skipme1234567890",
            verdict="skip",
            primary_topics=["RAG benchmarks"],
            channel_names=["ChannelA", "ChannelB"],
            reasoning="Overlapped with brief pa_abc.",
        )
        _insert_skip_defer_candidate(
            conn,
            candidate_id="cand_defer123abcdef00",
            verdict="defer",
            primary_topics=["AgenticOS"],
            channel_names=["ChannelC"],
            reasoning="Defer until a second channel covers this.",
        )
        text = recent_briefs_index.build_recent_briefs_index(conn)
        assert "## [SKIP]" in text
        assert "## [DEFER]" in text
        assert "Overlapped with brief pa_abc." in text
        assert "Defer until a second channel covers this." in text

    def test_mixed_ship_and_skip_orders_by_recency(self):
        conn = _make_conn()
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(hours=10)).isoformat()
        new_ts = (now - timedelta(hours=1)).isoformat()
        _insert_ship_artifact(
            conn,
            artifact_id="pa_old00000000aaaa",
            title="Older brief",
            thesis="Older.",
            candidate_id="cand_old111111111111",
            key_entities=["X"],
            ship_reasoning="old",
            updated_at=old_ts,
        )
        _insert_skip_defer_candidate(
            conn,
            candidate_id="cand_new111111111111",
            verdict="skip",
            primary_topics=["Newer skip"],
            channel_names=["ChannelZ"],
            reasoning="newer",
            evaluated_at=new_ts,
        )
        text = recent_briefs_index.build_recent_briefs_index(conn)
        # Newer skip should appear before older ship.
        skip_idx = text.find("[SKIP]")
        ship_idx = text.find("[SHIP]")
        assert skip_idx != -1 and ship_idx != -1
        assert skip_idx < ship_idx


class TestPruning:
    def test_lookback_excludes_old_entries(self):
        conn = _make_conn()
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        _insert_ship_artifact(
            conn,
            artifact_id="pa_ancient0000aaaa",
            title="Ancient",
            thesis="Old.",
            candidate_id="cand_ancient0000aaaa",
            key_entities=[],
            ship_reasoning="",
            updated_at=old_ts,
        )
        text = recent_briefs_index.build_recent_briefs_index(
            conn, lookback_hours=24
        )
        assert "Ancient" not in text

    def test_limit_caps_entry_count(self):
        conn = _make_conn()
        for idx in range(5):
            _insert_ship_artifact(
                conn,
                artifact_id=f"pa_cap{idx:013d}",
                title=f"Brief {idx}",
                thesis=f"Thesis {idx}.",
                candidate_id=f"cand_cap{idx:013d}",
                key_entities=["E"],
                ship_reasoning="r",
            )
        text = recent_briefs_index.build_recent_briefs_index(conn, limit=2)
        # Only 2 [SHIP] tokens should appear.
        assert text.count("[SHIP]") == 2


class TestAppendVerdict:
    def test_append_creates_file_if_missing(self, tmp_path: Path):
        path = tmp_path / "index.md"
        recent_briefs_index.append_verdict_to_index(
            index_path=path,
            artifact_id="pa_append00000001",
            candidate_id="cand_append00000001",
            verdict="ship",
            thesis="Test thesis.",
            key_entities=["Foo", "Bar"],
            ship_reasoning="Why ship.",
            operator_rating=None,
            decided_at="2026-05-28T12:00:00+00:00",
            title="Appended brief",
        )
        text = path.read_text(encoding="utf-8")
        assert "## [SHIP] Appended brief" in text
        assert "candidate_id: cand_append00000001" in text
        assert "artifact_id: pa_append00000001" in text
        assert "# Recent Intel Briefs Index" in text

    def test_append_to_existing_file(self, tmp_path: Path):
        conn = _make_conn()
        path = tmp_path / "index.md"
        recent_briefs_index.write_recent_briefs_index(conn, index_path=path)
        recent_briefs_index.append_verdict_to_index(
            index_path=path,
            artifact_id="pa_append00000002",
            candidate_id="cand_append00000002",
            verdict="skip",
            thesis="Skipped because of overlap.",
            key_entities=["Foo"],
            ship_reasoning="Overlaps with prior brief.",
            operator_rating=None,
            decided_at="2026-05-28T13:00:00+00:00",
            title="Skip cluster",
        )
        text = path.read_text(encoding="utf-8")
        assert "## [SKIP] Skip cluster" in text
        # Header preserved.
        assert "# Recent Intel Briefs Index" in text

    def test_append_self_prunes_to_max_entries(self, tmp_path: Path, monkeypatch):
        # Unbounded append was the index-growth bug; the appender now self-prunes.
        monkeypatch.setenv("UA_RECENT_BRIEFS_INDEX_MAX_ENTRIES", "50")
        path = tmp_path / "index.md"
        for n in range(250):
            recent_briefs_index.append_verdict_to_index(
                index_path=path,
                artifact_id=f"pa_{n:016d}",
                candidate_id=f"cand_{n:016d}",
                verdict="skip",
                thesis=f"thesis {n}",
                key_entities=["E"],
                ship_reasoning=f"reason {n}",
                operator_rating=None,
                decided_at="2026-06-04T00:00:00+00:00",
                title=f"C{n}",
            )
        text = path.read_text(encoding="utf-8")
        assert text.count("## [") == 50  # bounded to the cap
        assert len(text.encode("utf-8")) < 60_000  # far below unbounded growth
        assert "cand_0000000000000249" in text  # newest kept
        assert "cand_0000000000000000" not in text  # oldest pruned
        assert "# Recent Intel Briefs Index" in text  # header present/refreshed
        # Over-cap path used the atomic rewrite and left no temp file behind.
        assert not (tmp_path / "index.md.tmp").exists()
        # Still round-trips through the reader without tripping the corruption guard.
        conn = _make_conn()
        out = recent_briefs_index.read_index_or_fallback(conn, index_path=path)
        assert "cand_0000000000000249" in out

    def test_append_prune_disabled_grows(self, tmp_path: Path, monkeypatch):
        # Kill-switch: 0 disables pruning (legacy append-forever behavior).
        monkeypatch.setenv("UA_RECENT_BRIEFS_INDEX_MAX_ENTRIES", "0")
        path = tmp_path / "index.md"
        for n in range(120):
            recent_briefs_index.append_verdict_to_index(
                index_path=path,
                artifact_id=f"pa_{n:016d}",
                candidate_id=f"cand_{n:016d}",
                verdict="skip",
                thesis="t",
                key_entities=["E"],
                ship_reasoning="r",
                operator_rating=None,
                decided_at="2026-06-04T00:00:00+00:00",
                title=f"C{n}",
            )
        assert path.read_text(encoding="utf-8").count("## [") == 120


class TestReadOrFallback:
    def test_missing_file_rebuilds_from_db(self, tmp_path: Path):
        conn = _make_conn()
        _insert_ship_artifact(
            conn,
            artifact_id="pa_rebuild00000001",
            title="Rebuilt brief",
            thesis="A rebuilt brief from DB.",
            candidate_id="cand_rebuild00000001",
            key_entities=["E"],
            ship_reasoning="r",
        )
        path = tmp_path / "nonexistent_index.md"
        text = recent_briefs_index.read_index_or_fallback(
            conn, index_path=path
        )
        assert "Rebuilt brief" in text
        assert "# Recent Intel Briefs Index" in text

    def test_corrupted_file_rebuilds_from_db(self, tmp_path: Path):
        conn = _make_conn()
        _insert_ship_artifact(
            conn,
            artifact_id="pa_corrupt000000aa",
            title="Recovered brief",
            thesis="Recovered from corruption.",
            candidate_id="cand_corrupt0000aa00",
            key_entities=[],
            ship_reasoning="",
        )
        path = tmp_path / "corrupt.md"
        path.write_text("garbage content with no header or separator", encoding="utf-8")
        text = recent_briefs_index.read_index_or_fallback(
            conn, index_path=path
        )
        assert "Recovered brief" in text

    def test_intact_file_is_returned_verbatim(self, tmp_path: Path):
        conn = _make_conn()
        path = tmp_path / "intact.md"
        recent_briefs_index.write_recent_briefs_index(conn, index_path=path)
        # Hand-edit to a known good shape.
        path.write_text(
            "# Recent Intel Briefs Index\nLast updated: x\nLookback: 48 hours\n\n---\n\n"
            "## [SHIP] Hand-edited\ncandidate_id: cand_handedited0001\n"
            "artifact_id: pa_handedited01\noperator_rating: null\n\n---\n",
            encoding="utf-8",
        )
        text = recent_briefs_index.read_index_or_fallback(
            conn, index_path=path
        )
        assert "Hand-edited" in text


class TestUpdateOperatorRating:
    def test_updates_ship_block(self, tmp_path: Path):
        path = tmp_path / "rating.md"
        recent_briefs_index.append_verdict_to_index(
            index_path=path,
            artifact_id="pa_rate00000000aa",
            candidate_id="cand_rate00000000aa",
            verdict="ship",
            thesis="Rateable.",
            key_entities=["X"],
            ship_reasoning="ship",
            operator_rating=None,
            decided_at="2026-05-28T14:00:00+00:00",
            title="Rateable brief",
        )
        recent_briefs_index.update_operator_rating_in_index(
            index_path=path, artifact_id="pa_rate00000000aa", rating=5
        )
        text = path.read_text(encoding="utf-8")
        assert "operator_rating: 5" in text
        assert "operator_rating: null" not in text

    def test_idempotent(self, tmp_path: Path):
        path = tmp_path / "rating2.md"
        recent_briefs_index.append_verdict_to_index(
            index_path=path,
            artifact_id="pa_idem00000000aa",
            candidate_id="cand_idem00000000aa",
            verdict="ship",
            thesis="Idem.",
            key_entities=[],
            ship_reasoning="",
            operator_rating=None,
            decided_at="2026-05-28T14:00:00+00:00",
            title="Idem brief",
        )
        recent_briefs_index.update_operator_rating_in_index(
            index_path=path, artifact_id="pa_idem00000000aa", rating=1
        )
        recent_briefs_index.update_operator_rating_in_index(
            index_path=path, artifact_id="pa_idem00000000aa", rating=1
        )
        text = path.read_text(encoding="utf-8")
        assert text.count("operator_rating: 1") == 1

    def test_unknown_artifact_id_noop(self, tmp_path: Path):
        path = tmp_path / "rating3.md"
        recent_briefs_index.append_verdict_to_index(
            index_path=path,
            artifact_id="pa_known00000000a",
            candidate_id="cand_known00000000",
            verdict="ship",
            thesis="t",
            key_entities=[],
            ship_reasoning="",
            operator_rating=None,
            decided_at="2026-05-28T14:00:00+00:00",
            title="K",
        )
        before = path.read_text(encoding="utf-8")
        recent_briefs_index.update_operator_rating_in_index(
            index_path=path, artifact_id="pa_unknown00000000", rating=5
        )
        after = path.read_text(encoding="utf-8")
        assert before == after

    def test_missing_file_noop(self, tmp_path: Path):
        path = tmp_path / "missing.md"
        # Should not raise.
        recent_briefs_index.update_operator_rating_in_index(
            index_path=path, artifact_id="pa_x", rating=5
        )
        assert not path.exists()


class TestSchemaAdditions:
    def test_verdict_columns_present_on_fresh_db(self):
        conn = _make_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(proactive_artifacts)").fetchall()}
        assert "verdict" in cols
        assert "verdict_reasoning" in cols

    def test_verdict_columns_added_to_existing_db(self):
        # Simulate a DB created with the original schema (no verdict cols).
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE proactive_artifacts (
                artifact_id TEXT PRIMARY KEY,
                artifact_type TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_ref TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'produced',
                delivery_state TEXT NOT NULL DEFAULT 'not_surfaced',
                priority INTEGER NOT NULL DEFAULT 2,
                artifact_uri TEXT NOT NULL DEFAULT '',
                artifact_path TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                topic_tags_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                feedback_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                surfaced_at TEXT NOT NULL DEFAULT '',
                accepted_at TEXT NOT NULL DEFAULT '',
                rejected_at TEXT NOT NULL DEFAULT '',
                archived_at TEXT NOT NULL DEFAULT ''
            );
            """
        )
        # Insert a row before migration.
        conn.execute(
            "INSERT INTO proactive_artifacts (artifact_id, artifact_type, source_kind, title, created_at, updated_at) "
            "VALUES ('pa_x', 'x', 'x', 'x', 'now', 'now')"
        )
        conn.commit()
        # Now run the migration.
        proactive_artifacts.ensure_schema(conn)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(proactive_artifacts)").fetchall()}
        assert "verdict" in cols
        assert "verdict_reasoning" in cols
        # Existing row still readable.
        row = conn.execute("SELECT * FROM proactive_artifacts WHERE artifact_id = 'pa_x'").fetchone()
        assert dict(row)["verdict"] == ""
        assert dict(row)["verdict_reasoning"] == ""

    def test_convergence_candidates_table_present(self):
        conn = _make_conn()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(convergence_candidates)").fetchall()}
        expected = {
            "candidate_id", "video_ids_json", "channel_names_json",
            "channel_count", "primary_topics_json", "signatures_json",
            "task_id", "verdict", "verdict_reasoning", "artifact_id",
            "detected_at", "evaluated_at", "created_at", "updated_at",
            "metadata_json",
        }
        assert expected.issubset(cols)

    def test_convergence_candidates_indexes_present(self):
        conn = _make_conn()
        # Indexes by name.
        names = set()
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='convergence_candidates'"
        ).fetchall():
            names.add(row[0])
        assert "idx_convergence_candidates_verdict" in names
        assert "idx_convergence_candidates_detected" in names
        assert "idx_convergence_candidates_task" in names
