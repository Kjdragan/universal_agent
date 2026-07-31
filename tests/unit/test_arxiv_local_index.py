"""Unit tests for ``services/arxiv_local_index.py`` (the 2026-07-10 429 RCA fix).

The local index exists so paper_to_podcast discovery makes ZERO live arXiv
API calls. These tests cover the three surfaces — OAI XML parsing, FTS
search, and the offline cache fallback — with no network anywhere.
"""

from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path

import pytest

from universal_agent.services import arxiv_local_index as ali

# Relative seed dates so the 12-month recency cutoff never ages these out
# (scripts/check_test_date_literals.py guard).
RECENT = (date.today() - timedelta(days=30)).isoformat()
RECENT_2 = (date.today() - timedelta(days=60)).isoformat()
ANCIENT = "2020-01-01"  # date-pinned-ok (deliberately far outside any window)


OAI_PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-07-11T00:00:00Z</responseDate>
  <request verb="ListRecords">https://export.arxiv.org/oai2</request>
  <ListRecords>
    <record>
      <header>
        <identifier>oai:arXiv.org:2506.01234</identifier>
        <datestamp>2026-06-02</datestamp>
        <setSpec>cs</setSpec>
      </header>
      <metadata>
        <arXiv xmlns="http://arxiv.org/OAI/arXiv/">
          <id>2506.01234</id>
          <created>2026-06-01</created>
          <authors>
            <author><keyname>Ada</keyname><forenames>Grace</forenames></author>
            <author><keyname>Turing</keyname><forenames>Alan</forenames></author>
          </authors>
          <title>Retrieval-Augmented Generation
             with  Fresh Indexes</title>
          <categories>cs.CL cs.AI</categories>
          <abstract>We study retrieval-augmented generation (RAG) systems
            and their grounding behaviour.</abstract>
        </arXiv>
      </metadata>
    </record>
    <record>
      <header status="deleted">
        <identifier>oai:arXiv.org:2401.00001</identifier>
        <datestamp>2026-06-02</datestamp>
      </header>
    </record>
    <resumptionToken cursor="0" completeListSize="2">tok-123</resumptionToken>
  </ListRecords>
</OAI-PMH>
"""

NO_RECORDS_PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-07-11T00:00:00Z</responseDate>
  <request verb="ListRecords">https://export.arxiv.org/oai2</request>
  <error code="noRecordsMatch">no records match</error>
</OAI-PMH>
"""

BAD_ARG_PAGE = NO_RECORDS_PAGE.replace("noRecordsMatch", "badArgument")


class TestParseOaiListrecords:
    def test_parses_records_and_token(self):
        records, token = ali.parse_oai_listrecords(OAI_PAGE.encode())
        assert token == "tok-123"
        assert len(records) == 1  # deleted record skipped
        rec = records[0]
        assert rec["paper_id"] == "2506.01234"
        # Whitespace collapsed across the newline in the raw title.
        assert rec["title"] == "Retrieval-Augmented Generation with Fresh Indexes"
        assert rec["authors"] == "Grace Ada, Alan Turing"
        assert rec["categories"] == "cs.CL cs.AI"
        assert rec["published"] == "2026-06-01"  # date-pinned-ok (static XML fixture)
        # No <updated> element -> falls back to the header datestamp.
        assert rec["updated"] == "2026-06-02"  # date-pinned-ok (static XML fixture)

    def test_no_records_match_is_empty_not_error(self):
        records, token = ali.parse_oai_listrecords(NO_RECORDS_PAGE.encode())
        assert records == [] and token == ""

    def test_other_oai_errors_raise(self):
        with pytest.raises(ValueError, match="badArgument"):
            ali.parse_oai_listrecords(BAD_ARG_PAGE.encode())


def _seed(conn, papers):
    ali.upsert_papers(
        conn,
        [
            {
                "paper_id": pid,
                "title": title,
                "abstract": abstract,
                "authors": "A. Author",
                "categories": "cs.AI",
                "published": published,
                "updated": published,
            }
            for pid, title, abstract, published in papers
        ],
    )


class TestSearchIndex:
    @pytest.fixture()
    def conn(self, tmp_path):
        return ali.connect_index(tmp_path / "idx.db")

    def test_relevant_paper_ranks_first_and_cutoff_applies(self, conn):
        _seed(
            conn,
            [
                ("2506.1", "Retrieval-augmented generation advances",
                 "RAG with retrieval quality metrics", RECENT),
                ("2505.2", "Diffusion models for images",
                 "Denoising diffusion probabilistic models", RECENT_2),
                ("2001.3", "Ancient retrieval-augmented generation",
                 "Old RAG paper outside the window", ANCIENT),
            ],
        )
        results = ali.search_index(
            conn, "Retrieval-augmented generation (RAG) advances", months=12, limit=10
        )
        ids = [r["paper_id"] for r in results]
        assert ids[0] == "2506.1"
        assert "2001.3" not in ids  # 12-month cutoff

    def test_hazardous_topic_strings_do_not_raise(self, conn):
        _seed(conn, [("2506.1", "A title", "An abstract", RECENT)])
        for topic in [
            'Vision "language" models',
            "Mixture-of-experts (MoE) — sparse!",
            "AND OR NOT NEAR",
            "(((",
        ]:
            ali.search_index(conn, topic, months=12, limit=5)  # must not raise

    def test_upsert_replaces_and_fts_stays_in_sync(self, conn):
        _seed(conn, [("2506.1", "Old title about diffusion", "x", RECENT)])
        _seed(conn, [("2506.1", "New title about retrieval", "x", RECENT)])
        assert ali.search_index(conn, "diffusion", months=12) == []
        results = ali.search_index(conn, "retrieval", months=12)
        assert [r["paper_id"] for r in results] == ["2506.1"]
        row_count = conn.execute("SELECT COUNT(*) AS c FROM papers").fetchone()["c"]
        assert row_count == 1

    def test_build_fts_query_drops_stopwords_and_quotes_tokens(self):
        query = ali.build_fts_query("Retrieval-augmented generation (RAG) advances")
        assert '"rag"' in query and '"retrieval"' in query
        assert "advances" not in query  # stopword for topic strings
        assert "(" not in query.replace('"', "")


class TestCacheFallback:
    def test_ranks_topic_relevant_cached_paper_first(self, tmp_path):
        (tmp_path / "2506.111.md").write_text(
            "# Retrieval-Augmented Generation Survey\n\nWe survey RAG retrieval.",
            encoding="utf-8",
        )
        (tmp_path / "2505.222.md").write_text(
            "# Diffusion Models\n\nDenoising diffusion for image generation.",
            encoding="utf-8",
        )
        results = ali.cache_fallback_candidates(
            "Retrieval-augmented generation (RAG) advances", limit=5,
            storage_path=tmp_path,
        )
        assert results and results[0]["paper_id"] == "2506.111"
        assert results[0]["title"] == "Retrieval-Augmented Generation Survey"

    def test_empty_cache_returns_empty(self, tmp_path):
        assert ali.cache_fallback_candidates("anything", storage_path=tmp_path) == []


class TestCli:
    def test_search_unavailable_when_db_missing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("UA_ARXIV_INDEX_DB", str(tmp_path / "absent.db"))
        rc = ali.main(["search", "--query", "rag"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["status"] == "unavailable"

    def test_search_ok_roundtrip(self, tmp_path, monkeypatch, capsys):
        db = tmp_path / "idx.db"
        monkeypatch.setenv("UA_ARXIV_INDEX_DB", str(db))
        _seed(
            ali.connect_index(db),
            [("2506.1", "Agentic AI architectures", "Multi-agent systems", RECENT)],
        )
        rc = ali.main(
            ["search", "--query", "Agentic AI architectures and multi-agent systems"]
        )
        payload = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert payload["status"] == "ok"
        assert payload["papers"][0]["paper_id"] == "2506.1"
        assert payload["index"]["paper_count"] == 1


class TestMonthWindows:
    def test_windows_cover_backfill_range_without_gaps(self):
        from datetime import date

        windows = ali._month_windows(3, date(2026, 7, 11))
        assert windows[0][0] == "2026-04-01"  # date-pinned-ok (fixed input date)
        assert windows[-1][1] == "2026-07-11"  # date-pinned-ok (fixed input date)
        # Contiguous: each window starts the day after the previous ends.
        for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
            from datetime import date as _d, timedelta as _td

            assert _d.fromisoformat(next_start) == _d.fromisoformat(prev_end) + _td(days=1)
