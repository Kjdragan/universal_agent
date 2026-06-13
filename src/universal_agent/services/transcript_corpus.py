"""Transcript corpus reader for the YouTube transcript persistence layer.

Architecture rationale
----------------------
The CSI enrichment pipeline (``csi_rss_semantic_enrich.py``) fetches each
YouTube transcript (avg ~13K chars), truncates to a ~12K head/middle/tail
excerpt for the analyzer LLM, then **discards the full body** — storing only
``transcript_chars`` and ``transcript_ref`` on ``rss_event_analysis``.

Downstream, the intel-brief author (``evaluate-and-author-intel-brief`` skill,
run by the ATLAS principal) synthesizes operator-facing briefs from
``signatures[*].key_claims`` — ~300 chars distilled from those transcripts.

This module exposes three pure, defensive helpers that let the brief author
work from the **full persisted transcript** (``youtube_transcripts`` table in
``csi.db``, written by ``_persist_transcript`` in the enrich pass) rather than
the truncated key_claims, without any gating-stage changes. The pattern:

  gate cheap on key_claims → author on full_transcript (if persisted)
  → fall back to key_claims when transcript unavailable

All functions are best-effort and **never raise to the caller** — they return
``None`` / the original input on any error, so a missing DB, missing table, or
network failure degrades gracefully without aborting a brief-authoring mission.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CSI_DB_PATH = "/var/lib/universal-agent/csi/csi.db"


def resolve_csi_db_path() -> str:
    """Return the path to csi.db, honoring ``CSI_DB_PATH`` env var.

    Mirrors the same precedence used in
    ``scripts/csi_convergence_sync.py::_csi_db_path``.
    """
    return os.getenv("CSI_DB_PATH", DEFAULT_CSI_DB_PATH)


def get_persisted_transcript(video_id: str, *, csi_db_path: str | None = None) -> str | None:
    """Return the full transcript text for *video_id* from the corpus, or None.

    Opens ``csi.db`` read-only; never mutates state. Returns ``None`` when:

    - The ``youtube_transcripts`` table does not exist yet (migration not
      applied to the live DB yet — degrade gracefully).
    - The row does not exist for this ``video_id``.
    - The DB file does not exist or cannot be opened.
    - Any other exception (logged at DEBUG level).
    """
    path = csi_db_path or resolve_csi_db_path()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT transcript_text FROM youtube_transcripts WHERE video_id = ?",
                (video_id,),
            ).fetchone()
            if row is None:
                return None
            text = str(row["transcript_text"] or "").strip()
            return text if text else None
        finally:
            conn.close()
    except Exception:
        logger.debug("get_persisted_transcript: could not read video_id=%s from %s", video_id, path)
        return None


def fetch_transcript_via_gateway(
    video_id: str,
    *,
    base_url: str | None = None,
    timeout: float = 120.0,
    max_chars: int = 180000,
) -> str | None:
    """Fetch a transcript from the UA gateway's YouTube ingest endpoint.

    POSTs to ``{base_url}/api/v1/youtube/ingest`` (default
    ``http://127.0.0.1:8002``) with an ``Authorization: Bearer <token>``
    header when a token is available. Uses ``httpx`` (already a project dep).

    Returns the transcript text if the response is 200 and the text is
    non-empty; ``None`` on any error, non-200 response, or empty body.
    This is a best-effort helper — callers must handle ``None``.
    """
    import httpx  # local import: only needed on the fetch path

    endpoint = f"{(base_url or 'http://127.0.0.1:8002').rstrip('/')}/api/v1/youtube/ingest"
    token = (
        os.getenv("UA_YOUTUBE_INGEST_TOKEN")
        or os.getenv("UA_INTERNAL_API_TOKEN")
        or ""
    )
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload: dict[str, Any] = {
        "video_id": video_id,
        "timeout_seconds": int(timeout),
        "max_chars": max_chars,
    }
    try:
        resp = httpx.post(endpoint, json=payload, headers=headers, timeout=timeout + 10)
        if resp.status_code != 200:
            return None
        text = resp.json().get("transcript_text")
        if not text or not str(text).strip():
            return None
        return str(text).strip()
    except Exception:
        logger.debug("fetch_transcript_via_gateway: failed for video_id=%s", video_id)
        return None


def load_full_sources_for_candidate(
    signatures: list[dict],
    *,
    csi_db_path: str | None = None,
    allow_refetch: bool = True,
) -> list[dict]:
    """Enrich a list of signature dicts with their full transcript text.

    For each signature dict (must have ``video_id``), returns a **new dict
    copy** with an added key ``full_transcript``:

    - Persisted corpus first (``youtube_transcripts`` in csi.db).
    - If ``None`` and ``allow_refetch`` is True, falls back to
      ``fetch_transcript_via_gateway``.
    - ``None`` when neither source has the text.

    All original keys are preserved unchanged. Input dicts are never mutated.
    On any per-signature error the signature is returned unchanged (with
    ``full_transcript=None``). Never raises.
    """
    results: list[dict] = []
    for sig in signatures:
        try:
            enriched = dict(sig)
            video_id = str(sig.get("video_id") or "").strip()
            if not video_id:
                enriched["full_transcript"] = None
                results.append(enriched)
                continue

            text = get_persisted_transcript(video_id, csi_db_path=csi_db_path)
            if text is None and allow_refetch:
                text = fetch_transcript_via_gateway(video_id)
            enriched["full_transcript"] = text
            results.append(enriched)
        except Exception:
            logger.debug("load_full_sources_for_candidate: error enriching sig=%s", sig)
            fallback = dict(sig)
            fallback["full_transcript"] = None
            results.append(fallback)
    return results
