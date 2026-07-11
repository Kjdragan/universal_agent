"""Local arXiv metadata index — rate-limit-proof paper DISCOVERY.

Why this exists
---------------
The ``paper_to_podcast_daily`` cron repeatedly died at step one: its single
live ``mcp__arxiv-mcp-server__search_papers`` call returned HTTP 429 because
arXiv rate-limits the VPS's shared IP *server-side*, keyed to cumulative
traffic from EVERY arXiv consumer on the box (the arxiv-specialist agent,
research paths, the p2p cron itself). Client-side pacing — what the
third-party ``arxiv-mcp-server`` provides — cannot prevent a server-side
per-IP throttle, and a hand-rolled client against the same public
``export.arxiv.org/api/query`` endpoint would hit the identical 429. The
durable fix is to change the ACCESS PATTERN: discovery moves to a local
metadata index harvested in bulk via arXiv's sanctioned OAI-PMH interface
(``export.arxiv.org/oai2``), so the daily pipeline makes ZERO live search
calls. Only the handful of selected papers are downloaded live (~5 req/day,
trivially under any limit). See the 2026-07-10 failed-run RCA
(``run_id 78c38721000a``).

Three surfaces, one module
--------------------------
* ``harvest`` — incremental OAI-PMH ``ListRecords`` (metadataPrefix=arXiv)
  by set + datestamp window into a SQLite FTS5 database. Resumable at
  month granularity for backfill; a daily systemd timer
  (``universal-agent-arxiv-index-harvest.timer``) keeps it fresh. A failed
  harvest merely leaves the index a day stale — the pipeline still works.
* ``search`` — FTS5 (bm25-ranked) topic search over title+abstract with a
  recency cutoff. Pure local read; this is what the paper-to-podcast skill
  calls for discovery.
* ``cache-fallback`` — deterministic keyword-overlap ranking over the
  already-downloaded full-text cache (``arxiv_runtime
  .canonical_arxiv_storage_path()``), so a run that cannot reach arXiv at
  all still assembles topic-relevant papers instead of no-op'ing.

Every subcommand prints a single JSON object to stdout (agent-friendly) and
exits 0 even on "index unavailable" — callers branch on ``status``, not on
exit codes.

CLI (run with the deployed venv python; PYTHONPATH=<repo>/src):

    python -m universal_agent.services.arxiv_local_index harvest --days 3
    python -m universal_agent.services.arxiv_local_index harvest --backfill-months 12
    python -m universal_agent.services.arxiv_local_index search --query "RAG advances" --months 12 --limit 15
    python -m universal_agent.services.arxiv_local_index cache-fallback --query "RAG advances" --limit 5
    python -m universal_agent.services.arxiv_local_index status
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterator, Optional
import urllib.error
import urllib.parse
import urllib.request

from defusedxml import ElementTree as SafeElementTree

from universal_agent.arxiv_runtime import canonical_arxiv_storage_path

logger = logging.getLogger(__name__)

OAI_ENDPOINT = "https://export.arxiv.org/oai2"
_OAI_NS = "{http://www.openarchives.org/OAI/2.0/}"
_ARXIV_NS = "{http://arxiv.org/OAI/arXiv/}"

# The p2p topic list is AI/ML; cs covers most, stat.ML and eess (speech/audio)
# fill the rest. Overridable per-invocation via --sets.
DEFAULT_OAI_SETS = ("cs", "stat", "eess")

# arXiv asks bulk harvesters for ~1 request / 3s; 4s keeps clear margin.
_POLITENESS_DELAY_SECONDS = 4.0
_HTTP_TIMEOUT_SECONDS = 60.0
_MAX_RETRIES_PER_REQUEST = 5
_DEFAULT_RETRY_AFTER_SECONDS = 30.0
_MAX_RETRY_AFTER_SECONDS = 300.0

_DEFAULT_DB_PATH = str(Path.home() / ".arxiv-local-index" / "arxiv_index.db")

# Tokens shorter than 2 chars are noise; a few stopwords that appear in every
# p2p topic string would otherwise match everything.
_QUERY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "into", "is", "it", "of", "on", "or", "the", "to", "via", "with",
    "advances", "techniques", "methods", "innovations", "frameworks",
}

_ABSTRACT_TRUNCATE_CHARS = 600


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def canonical_index_db_path() -> Path:
    """Resolve the ONE SQLite file the harvester writes and search reads.

    Priority: ``UA_ARXIV_INDEX_DB`` env var, else
    ``~/.arxiv-local-index/arxiv_index.db`` (mirrors the
    ``arxiv_runtime.canonical_arxiv_storage_path`` home-dir convention so the
    index survives ``/opt/universal_agent`` deploys on the VPS).
    """
    raw = str(os.getenv("UA_ARXIV_INDEX_DB") or "").strip()
    path = Path(raw if raw else _DEFAULT_DB_PATH).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("arxiv_local_index: could not create %s: %s", path.parent, exc)
    return path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    paper_id   TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    abstract   TEXT NOT NULL DEFAULT '',
    authors    TEXT NOT NULL DEFAULT '',
    categories TEXT NOT NULL DEFAULT '',
    published  TEXT NOT NULL DEFAULT '',
    updated    TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract, content='papers', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract)
    VALUES (new.rowid, new.title, new.abstract);
END;
CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
    VALUES ('delete', old.rowid, old.title, old.abstract);
END;
CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract)
    VALUES ('delete', old.rowid, old.title, old.abstract);
    INSERT INTO papers_fts(rowid, title, abstract)
    VALUES (new.rowid, new.title, new.abstract);
END;
CREATE TABLE IF NOT EXISTS harvest_state (
    set_spec       TEXT PRIMARY KEY,
    last_datestamp TEXT NOT NULL
);
"""


def connect_index(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (creating if needed) the index database with schema applied."""
    path = db_path or canonical_index_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def upsert_papers(conn: sqlite3.Connection, records: list[dict[str, str]]) -> int:
    """Insert-or-replace parsed OAI records. Returns the number written."""
    rows = [
        (
            r["paper_id"], r["title"], r["abstract"], r["authors"],
            r["categories"], r["published"], r["updated"],
        )
        for r in records
        if r.get("paper_id") and r.get("title")
    ]
    with conn:
        # DELETE+INSERT (not INSERT OR REPLACE) so the FTS delete trigger sees
        # the old row content — REPLACE's implicit delete fires the AD trigger
        # too, but being explicit keeps the FTS contentful-sync obvious.
        conn.executemany(
            "DELETE FROM papers WHERE paper_id = ?", [(row[0],) for row in rows]
        )
        conn.executemany(
            "INSERT INTO papers (paper_id, title, abstract, authors, categories,"
            " published, updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def index_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Cheap observability snapshot: row count, freshness, per-set state."""
    paper_count = conn.execute("SELECT COUNT(*) AS c FROM papers").fetchone()["c"]
    latest = conn.execute(
        "SELECT MAX(published) AS m FROM papers"
    ).fetchone()["m"]
    state = {
        row["set_spec"]: row["last_datestamp"]
        for row in conn.execute("SELECT set_spec, last_datestamp FROM harvest_state")
    }
    return {
        "paper_count": paper_count,
        "latest_published": latest or "",
        "harvest_state": state,
    }


# ---------------------------------------------------------------------------
# OAI-PMH harvest
# ---------------------------------------------------------------------------

def _collapse_ws(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_oai_listrecords(xml_bytes: bytes) -> tuple[list[dict[str, str]], str]:
    """Parse one OAI-PMH ListRecords page (metadataPrefix=arXiv).

    Returns ``(records, resumption_token)``; the token is ``""`` on the last
    page. Records with header ``status="deleted"`` carry no metadata and are
    skipped. Raises ``ValueError`` on an OAI-level ``<error>`` EXCEPT
    ``noRecordsMatch``, which legitimately means an empty window and returns
    ``([], "")``.
    """
    root = SafeElementTree.fromstring(xml_bytes)
    error = root.find(f"{_OAI_NS}error")
    if error is not None:
        code = error.get("code", "")
        if code == "noRecordsMatch":
            return [], ""
        raise ValueError(f"OAI error {code}: {_collapse_ws(error.text)}")

    records: list[dict[str, str]] = []
    list_records = root.find(f"{_OAI_NS}ListRecords")
    if list_records is None:
        return [], ""
    for record in list_records.findall(f"{_OAI_NS}record"):
        header = record.find(f"{_OAI_NS}header")
        if header is None or header.get("status") == "deleted":
            continue
        meta = record.find(f"{_OAI_NS}metadata/{_ARXIV_NS}arXiv")
        if meta is None:
            continue
        authors = []
        for author in meta.findall(f"{_ARXIV_NS}authors/{_ARXIV_NS}author"):
            keyname = _collapse_ws(author.findtext(f"{_ARXIV_NS}keyname"))
            forenames = _collapse_ws(author.findtext(f"{_ARXIV_NS}forenames"))
            full = f"{forenames} {keyname}".strip()
            if full:
                authors.append(full)
        records.append(
            {
                "paper_id": _collapse_ws(meta.findtext(f"{_ARXIV_NS}id")),
                "title": _collapse_ws(meta.findtext(f"{_ARXIV_NS}title")),
                "abstract": _collapse_ws(meta.findtext(f"{_ARXIV_NS}abstract")),
                "authors": ", ".join(authors),
                "categories": _collapse_ws(meta.findtext(f"{_ARXIV_NS}categories")),
                "published": _collapse_ws(meta.findtext(f"{_ARXIV_NS}created")),
                "updated": _collapse_ws(meta.findtext(f"{_ARXIV_NS}updated"))
                or _collapse_ws(header.findtext(f"{_OAI_NS}datestamp")),
            }
        )
    token_el = list_records.find(f"{_OAI_NS}resumptionToken")
    token = (token_el.text or "").strip() if token_el is not None else ""
    return records, token


def _fetch_oai_page(url: str) -> bytes:
    """GET one OAI page with 503/429 Retry-After handling.

    Blocking sleeps are fine here: harvest runs inside a oneshot systemd
    service, never inside an agent turn.
    """
    last_error: Exception = RuntimeError("unreachable")
    for attempt in range(1, _MAX_RETRIES_PER_REQUEST + 1):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "universal-agent-arxiv-local-index/1.0"}
            )
            with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in (503, 429) and attempt < _MAX_RETRIES_PER_REQUEST:
                retry_after_raw = exc.headers.get("Retry-After", "")
                try:
                    delay = float(retry_after_raw)
                except (TypeError, ValueError):
                    delay = _DEFAULT_RETRY_AFTER_SECONDS
                delay = min(max(delay, 1.0), _MAX_RETRY_AFTER_SECONDS)
                logger.info(
                    "arxiv_local_index: HTTP %s, retrying in %.0fs (attempt %d/%d)",
                    exc.code, delay, attempt, _MAX_RETRIES_PER_REQUEST,
                )
                time.sleep(delay)
                continue
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            if attempt < _MAX_RETRIES_PER_REQUEST:
                time.sleep(_DEFAULT_RETRY_AFTER_SECONDS)
                continue
            raise
    raise last_error


def _iter_oai_records(
    set_spec: str, from_date: str, until_date: str
) -> Iterator[list[dict[str, str]]]:
    """Yield pages of parsed records for one set + datestamp window."""
    params = {
        "verb": "ListRecords",
        "metadataPrefix": "arXiv",
        "set": set_spec,
        "from": from_date,
        "until": until_date,
    }
    url = f"{OAI_ENDPOINT}?{urllib.parse.urlencode(params)}"
    while True:
        records, token = parse_oai_listrecords(_fetch_oai_page(url))
        if records:
            yield records
        if not token:
            return
        url = f"{OAI_ENDPOINT}?" + urllib.parse.urlencode(
            {"verb": "ListRecords", "resumptionToken": token}
        )
        time.sleep(_POLITENESS_DELAY_SECONDS)


def harvest_window(
    conn: sqlite3.Connection,
    sets: tuple[str, ...],
    from_date: str,
    until_date: str,
) -> dict[str, int]:
    """Harvest one datestamp window for each set, updating harvest_state.

    Per-set state commits after the set completes, so a crash mid-backfill
    resumes at set granularity. Returns ``{set_spec: records_written}``.
    """
    written: dict[str, int] = {}
    for set_spec in sets:
        count = 0
        for page in _iter_oai_records(set_spec, from_date, until_date):
            count += upsert_papers(conn, page)
        with conn:
            conn.execute(
                "INSERT INTO harvest_state (set_spec, last_datestamp) VALUES (?, ?)"
                " ON CONFLICT(set_spec) DO UPDATE SET last_datestamp ="
                " MAX(last_datestamp, excluded.last_datestamp)",
                (set_spec, until_date),
            )
        written[set_spec] = count
        logger.info(
            "arxiv_local_index: harvested %s records for set=%s %s..%s",
            count, set_spec, from_date, until_date,
        )
    return written


def _month_windows(months_back: int, today: date) -> list[tuple[str, str]]:
    """Month-granularity (from, until) date pairs, oldest first."""
    windows: list[tuple[str, str]] = []
    # First day of the month `months_back` months ago.
    year, month = today.year, today.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    cursor = date(year, month, 1)
    while cursor <= today:
        if cursor.month == 12:
            month_end = date(cursor.year, 12, 31)
        else:
            month_end = date(cursor.year, cursor.month + 1, 1) - timedelta(days=1)
        windows.append((cursor.isoformat(), min(month_end, today).isoformat()))
        month_end_next = month_end + timedelta(days=1)
        cursor = month_end_next
    return windows


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def build_fts_query(topic: str) -> str:
    """Turn a free-text topic into a safe FTS5 OR-query.

    Topic strings contain FTS5 syntax hazards (parentheses, hyphens, quotes)
    — e.g. "Retrieval-augmented generation (RAG) advances" — so we reduce to
    bare word tokens, drop stopwords, quote each token, and OR them. bm25
    ranking then surfaces papers matching more/rarer terms first.
    """
    tokens = re.findall(r"[A-Za-z0-9]+", topic.lower())
    kept = [t for t in tokens if len(t) >= 2 and t not in _QUERY_STOPWORDS]
    if not kept:
        kept = [t for t in tokens if t]
    # Dedup preserving order.
    seen: set[str] = set()
    unique = [t for t in kept if not (t in seen or seen.add(t))]
    return " OR ".join(f'"{t}"' for t in unique)


def search_index(
    conn: sqlite3.Connection,
    topic: str,
    months: int = 12,
    limit: int = 15,
) -> list[dict[str, Any]]:
    """bm25-ranked FTS search filtered to papers published within `months`."""
    fts_query = build_fts_query(topic)
    if not fts_query:
        return []
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=months * 31)).isoformat()
    rows = conn.execute(
        """
        SELECT p.paper_id, p.title, p.abstract, p.authors, p.categories,
               p.published, p.updated, bm25(papers_fts) AS rank
        FROM papers_fts
        JOIN papers p ON p.rowid = papers_fts.rowid
        WHERE papers_fts MATCH ? AND p.published >= ?
        ORDER BY rank
        LIMIT ?
        """,
        (fts_query, cutoff, limit),
    ).fetchall()
    return [
        {
            "paper_id": row["paper_id"],
            "title": row["title"],
            "authors": row["authors"],
            "categories": row["categories"],
            "published": row["published"],
            "updated": row["updated"],
            "abstract": row["abstract"][:_ABSTRACT_TRUNCATE_CHARS],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Cache fallback (offline last resort)
# ---------------------------------------------------------------------------

def _cached_paper_head(path: Path, max_chars: int = 2000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return fh.read(max_chars)
    except OSError:
        return ""


def _extract_title_from_markdown(head: str, fallback: str) -> str:
    for line in head.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("# ").strip() or fallback
        if stripped:
            return stripped[:200]
    return fallback


def cache_fallback_candidates(
    topic: str,
    limit: int = 5,
    storage_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Rank the already-downloaded full-text cache by topic-term overlap.

    Deterministic last resort when BOTH the local index and live arXiv are
    unavailable: a podcast from previously-downloaded topic-relevant papers
    beats a no-op run. Score = fraction of distinct topic terms present in
    the paper's head (title + opening text), title hits weighted double.
    Ties break on paper_id (newest arXiv ids sort last, so reverse).
    """
    directory = storage_path or canonical_arxiv_storage_path()
    tokens = re.findall(r"[A-Za-z0-9]+", topic.lower())
    terms = {t for t in tokens if len(t) >= 2 and t not in _QUERY_STOPWORDS}
    if not terms:
        terms = set(tokens)
    scored: list[tuple[float, str, dict[str, Any]]] = []
    try:
        candidates = sorted(directory.glob("*.md"))
    except OSError:
        return []
    for md_path in candidates:
        head = _cached_paper_head(md_path)
        if not head:
            continue
        title = _extract_title_from_markdown(head, fallback=md_path.stem)
        title_lower = title.lower()
        head_lower = head.lower()
        score = sum(
            2.0 if term in title_lower else (1.0 if term in head_lower else 0.0)
            for term in terms
        ) / (2.0 * len(terms))
        if score <= 0:
            continue
        scored.append(
            (
                score,
                md_path.stem,
                {
                    "paper_id": md_path.stem,
                    "title": title,
                    "path": str(md_path),
                    "score": round(score, 3),
                },
            )
        )
    # Highest score first; among equal scores, newest arXiv id first (two
    # stable sorts — string ids can't be negated into one composite key).
    scored.sort(key=lambda item: item[1], reverse=True)
    scored.sort(key=lambda item: -item[0])
    return [item[2] for item in scored[:limit]]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _cmd_harvest(args: argparse.Namespace) -> int:
    sets = tuple(s.strip() for s in args.sets.split(",") if s.strip())
    conn = connect_index()
    today = datetime.now(timezone.utc).date()
    totals: dict[str, int] = {}
    if args.backfill_months:
        windows = _month_windows(args.backfill_months, today)
    else:
        start = today - timedelta(days=args.days)
        windows = [(start.isoformat(), today.isoformat())]
    for from_date, until_date in windows:
        written = harvest_window(conn, sets, from_date, until_date)
        for set_spec, count in written.items():
            totals[set_spec] = totals.get(set_spec, 0) + count
    _emit({"status": "ok", "written": totals, "index": index_status(conn)})
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    db_path = canonical_index_db_path()
    if not db_path.is_file():
        _emit({
            "status": "unavailable",
            "reason": f"index database missing at {db_path} — run harvest first",
        })
        return 0
    conn = connect_index(db_path)
    status = index_status(conn)
    if status["paper_count"] == 0:
        _emit({"status": "unavailable", "reason": "index is empty — run harvest first"})
        return 0
    papers = search_index(conn, args.query, months=args.months, limit=args.limit)
    _emit({
        "status": "ok" if papers else "no_matches",
        "query": args.query,
        "result_count": len(papers),
        "index": status,
        "papers": papers,
    })
    return 0


def _cmd_cache_fallback(args: argparse.Namespace) -> int:
    papers = cache_fallback_candidates(args.query, limit=args.limit)
    _emit({
        "status": "ok" if papers else "no_matches",
        "query": args.query,
        "result_count": len(papers),
        "papers": papers,
    })
    return 0


def _cmd_status(args: argparse.Namespace) -> int:  # noqa: ARG001
    db_path = canonical_index_db_path()
    if not db_path.is_file():
        _emit({"status": "unavailable", "db_path": str(db_path)})
        return 0
    conn = connect_index(db_path)
    _emit({"status": "ok", "db_path": str(db_path), "index": index_status(conn)})
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="arxiv_local_index",
        description="Local arXiv metadata index: OAI-PMH harvest + FTS search.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_harvest = sub.add_parser("harvest", help="Harvest OAI-PMH metadata into the index")
    p_harvest.add_argument("--days", type=int, default=3,
                           help="Incremental window: harvest the last N days (default 3)")
    p_harvest.add_argument("--backfill-months", type=int, default=0,
                           help="One-time backfill: harvest month-by-month for N months")
    p_harvest.add_argument("--sets", default=",".join(DEFAULT_OAI_SETS),
                           help="Comma-separated OAI sets (default cs,stat,eess)")
    p_harvest.set_defaults(func=_cmd_harvest)

    p_search = sub.add_parser("search", help="Search the local index (no network)")
    p_search.add_argument("--query", required=True)
    p_search.add_argument("--months", type=int, default=12)
    p_search.add_argument("--limit", type=int, default=15)
    p_search.set_defaults(func=_cmd_search)

    p_fallback = sub.add_parser(
        "cache-fallback",
        help="Rank the downloaded full-text cache by topic relevance (offline)",
    )
    p_fallback.add_argument("--query", required=True)
    p_fallback.add_argument("--limit", type=int, default=5)
    p_fallback.set_defaults(func=_cmd_cache_fallback)

    p_status = sub.add_parser("status", help="Index freshness / row count")
    p_status.set_defaults(func=_cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
