"""Score CSI demo-triage candidates with a single GLM call.

Run periodically by the ``csi_demo_triage_rank`` cron (registered in
gateway_server). The dashboard "Rerank" button also calls this directly
via the ``/triage/rerank`` endpoint.

Selects pending candidates whose ``ranking_score`` is NULL, or whose
``ranking_evaluated_at`` is older than ``rescore_after_hours`` (default
24h). Issues a single LLM call with a numbered list of all selected
candidates and parses the response line-by-line as ``{"post_id", "score",
"rationale"}`` JSON objects. Tolerates malformed lines.

Pacing: wraps the LLM call in ``csi_llm_pacing.paced_llm_call`` if that
module is importable on this branch. Falls back to a direct call
otherwise (the pacing module ships on a different branch).
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from typing import Any, Iterator
import uuid

import httpx

from universal_agent.services import csi_demo_triage

logger = logging.getLogger(__name__)


_DEFAULT_MODEL = "glm-4.6"


_RANK_SYSTEM_PROMPT = """You are scoring potential Claude Code demos and intelligence opportunities for an internal triage queue. Each candidate is a tweet from an Anthropic-adjacent account that an upstream classifier has flagged as worthy of further investment.

For EACH candidate below, output a single line of JSON: {"post_id": "...", "score": 0.0, "rationale": "..."}

Score 0-10 (one decimal place) based on:

1. CONCRETENESS - Does the source describe a SPECIFIC feature, command, or capability that could be exercised in code? Vague hype scores low; named features (e.g. "/fewer-permission-prompts skill") score high.

2. AUTHORITY - Official Anthropic accounts (@ClaudeDevs, @bcherny, etc.) score higher than community speculation. Reposts of official content also count.

3. BUILDABILITY - Can a coding agent (Cody) actually exercise this feature TODAY against the live API/CLI? Speculative roadmap items score low; shipped capabilities score high.

4. NOVELTY - Does this teach us something we don't already have a demo for? Repeated feature mentions score lower than first-time announcements.

For TIER 4 candidates (intel/analysis tasks for Atlas, not demos), score on the same axes but weighted toward AUTHORITY and NOVELTY (the agent reads + summarizes, doesn't build).

Be SKEPTICAL. The default should be 3-6. Reserve 8+ for clearly buildable, official-source, novel demos. Reserve <2 for off-topic / promotional / pure-speculation.

Rationale: 1-2 sentences, plain English, why this score. Mention the deciding factor.

Output ONLY the JSON lines, one per candidate, no preamble or wrapping array.
"""


@dataclass
class RankingResult:
    run_id: str
    started_at: str
    finished_at: str
    candidates_scored: int
    candidates_skipped: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "candidates_scored": int(self.candidates_scored),
            "candidates_skipped": int(self.candidates_skipped),
            "error": self.error,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _pacing_ctx(stage: str) -> Iterator[None]:
    """Wrap the LLM call with `paced_llm_call` if available; no-op otherwise."""
    try:
        from universal_agent.services.csi_llm_pacing import paced_llm_call

        with paced_llm_call(stage=stage):
            yield
        return
    except Exception:
        with nullcontext():
            yield


def _build_user_message(candidates: list[csi_demo_triage.TriageCandidate]) -> str:
    lines: list[str] = ["Score the following candidates:", ""]
    for idx, cand in enumerate(candidates, start=1):
        text = (cand.post_text or "").strip().replace("\n", " ")
        if len(text) > 500:
            text = text[:497] + "..."
        links = list(cand.linked_sources or [])
        link_summary = f"{len(links)} link(s)"
        if links:
            link_summary += f"; first: {links[0]}"
        lines.extend(
            [
                f"### Candidate {idx}",
                f"post_id: {cand.post_id}",
                f"tier: {cand.tier}",
                f"handle: @{cand.handle}",
                f"action_type: {cand.action_type}",
                f"linked_sources: {link_summary}",
                f"post_text: {text}",
                "",
            ]
        )
    return "\n".join(lines)


def _select_candidates(
    conn,
    *,
    rescore_after_hours: float,
    max_candidates: int,
) -> list[csi_demo_triage.TriageCandidate]:
    """Pending rows that are unranked OR have stale ranking."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=max(0.0, rescore_after_hours))
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """
        SELECT * FROM demo_triage_candidates
        WHERE state = 'pending'
          AND (ranking_score IS NULL
               OR ranking_evaluated_at IS NULL
               OR ranking_evaluated_at < ?)
        ORDER BY first_seen_at DESC
        LIMIT ?
        """,
        (cutoff, int(max(1, max_candidates))),
    ).fetchall()
    return [csi_demo_triage._row_to_candidate(r) for r in rows]


def _call_llm(*, system: str, user: str, timeout: float = 90.0) -> str:
    """POST to the configured Anthropic-compatible endpoint and return text."""
    base_url = (os.getenv("ANTHROPIC_BASE_URL") or "https://api.z.ai/api/anthropic").rstrip("/")
    auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY") or ""
    model = os.getenv("UA_CSI_DEMO_TRIAGE_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    if not auth_token:
        raise RuntimeError("ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY not set")
    headers = {
        "x-api-key": auth_token,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{base_url}/v1/messages", json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    parts = data.get("content") or []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "text":
            chunks.append(str(part.get("text") or ""))
    return "".join(chunks).strip()


def _parse_lines(raw: str) -> list[dict[str, Any]]:
    """Parse one JSON object per line. Tolerate junk lines."""
    out: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("```"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("triage_rank: skipping malformed line: %s", line[:120])
            continue
        if not isinstance(obj, dict):
            continue
        if "post_id" not in obj or "score" not in obj:
            continue
        out.append(obj)
    return out


def run_ranking(
    *,
    conn=None,
    artifacts_root=None,
    rescore_after_hours: float = 24.0,
    max_candidates: int = 60,
) -> RankingResult:
    """Score all unscored / stale-scored pending candidates in one LLM call."""
    run_id = uuid.uuid4().hex[:16]
    started_at = _now_iso()
    own_conn = conn is None
    if conn is None:
        conn = csi_demo_triage.open_db(artifacts_root)
    else:
        csi_demo_triage.ensure_schema(conn)
    try:
        candidates = _select_candidates(
            conn,
            rescore_after_hours=rescore_after_hours,
            max_candidates=max_candidates,
        )
        if not candidates:
            return RankingResult(
                run_id=run_id,
                started_at=started_at,
                finished_at=_now_iso(),
                candidates_scored=0,
                candidates_skipped=0,
                error=None,
            )

        user_msg = _build_user_message(candidates)
        try:
            with _pacing_ctx(stage="demo_triage_rank"):
                raw = _call_llm(system=_RANK_SYSTEM_PROMPT, user=user_msg)
        except Exception as exc:
            logger.exception("triage_rank: LLM call failed")
            return RankingResult(
                run_id=run_id,
                started_at=started_at,
                finished_at=_now_iso(),
                candidates_scored=0,
                candidates_skipped=len(candidates),
                error=f"{type(exc).__name__}: {exc}",
            )

        parsed = _parse_lines(raw)
        by_id = {str(p.get("post_id") or "").strip(): p for p in parsed if p.get("post_id")}

        scored = 0
        skipped = 0
        now = _now_iso()
        for cand in candidates:
            entry = by_id.get(cand.post_id)
            if not entry:
                skipped += 1
                continue
            try:
                score = float(entry.get("score"))
            except (TypeError, ValueError):
                skipped += 1
                continue
            score = max(0.0, min(10.0, round(score, 1)))
            rationale = str(entry.get("rationale") or "").strip()[:1000]
            conn.execute(
                """
                UPDATE demo_triage_candidates
                   SET ranking_score = ?,
                       ranking_rationale = ?,
                       ranking_evaluated_at = ?,
                       ranking_run_id = ?
                 WHERE post_id = ?
                """,
                (score, rationale, now, run_id, cand.post_id),
            )
            scored += 1
        conn.commit()

        return RankingResult(
            run_id=run_id,
            started_at=started_at,
            finished_at=_now_iso(),
            candidates_scored=scored,
            candidates_skipped=skipped,
            error=None,
        )
    finally:
        if own_conn:
            conn.close()
