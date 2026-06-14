"""Reusable structured-output BATCHING helper for per-item ZAI LLM fan-outs.

WHY THIS EXISTS (2026-06-13): ZAI Fair-Usage rejection is driven by request
**concurrency/frequency**, not token volume — a call sent while another is in
flight rejects ~77% vs ~10% with nothing in flight. The lever is therefore
*fewer CALLS*. PR #989 proved the pattern on the convergence cluster judge: a
``for item in items:`` loop of N independent per-item LLM calls collapses to
**one structured-output call per chunk of ~20 items**, returning a verdict array
keyed by item index. A live batch-size sweep (61 buckets vs adjudicated truth)
found ~20/call is an inverted-U sweet spot — it beats both per-item (F1 0.78, 61
calls) and one-giant-call (F1 0.67, 1 call) at **F1 0.84 / 4 calls / ~half the
tokens**: moderate batching gives the judge cross-item comparative context that
*sharpens* precision without diluting attention.

This module extracts the *mechanics* of that pattern so each new site doesn't
re-derive (and drift on) chunking, verdict-array parsing, fail-closed defaults,
the FUP circuit breaker, and shared-context-once. It owns the mechanics; the
call-site keeps the *semantics* (prompt content, schema, per-item cache, and the
precision/eligibility gate — which lives inside ``parse``).

WHAT THE HELPER OWNS
  - chunking at ``batch_size``;
  - the sequential / bounded fan-out under ``asyncio.Semaphore(concurrency)``
    (default 1 — storm-avoidance; do NOT raise without a measured need);
  - one ``call_llm`` per chunk with the shared ``system`` + ``model_overrides``;
  - ``parse_response`` (fence/trailing-junk tolerant JSON);
  - mapping the ``{"verdicts": [{index, ...}]}`` array back to items by index,
    incl. the single-item **bare-verdict fallback** (a model may answer a 1-item
    chunk with a bare object and no ``verdicts`` array);
  - substituting ``fail_closed`` on any NON-FUP error / parse-miss — per CHUNK
    (you got no verdicts back, so the whole chunk fails closed);
  - the one-shot **FUP circuit breaker**: a Fair-Usage ([1313]) signal RE-RAISES
    out of the chunk and trips a flag that skips every remaining chunk without a
    call (re-detected next idempotent run);
  - the wall-clock ``deadline`` (monotonic seconds), checked before each chunk.

WHAT EACH CALL-SITE KEEPS
  - ``build_prompt(chunk) -> str``: the per-chunk user payload, INCLUDING the
    per-item index assignment (items are indexed by their position in ``chunk``);
  - the output schema (documented in the prompt);
  - the per-item cache: filter to UNCACHED items BEFORE calling, and ``cache_put``
    AFTER — **only for results whose ``.ok`` is True**. ``ok=False`` means the
    value is a fail-closed substitution (the call failed), which must NEVER be
    cached (caching it would suppress a real item until the TTL expires);
  - the gate: any precision/eligibility logic lives inside ``parse``.

Returns a list aligned 1:1 with ``items``: each a :class:`BatchedResult` whose
``.value`` is the caller's parsed result (possibly ``None`` for a clean negative)
or the ``fail_closed`` substitution, and whose ``.ok`` distinguishes a real
verdict (cacheable) from a fail-closed substitution (not cacheable).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Any, Awaitable, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 20


@dataclass
class BatchedResult:
    """One item's outcome from :func:`batched_judge`.

    ``value`` — the caller's ``parse`` result (may be ``None`` for a clean
    negative), OR the ``fail_closed`` substitution when the item could not be
    judged.
    ``ok`` — ``True`` when a real verdict was obtained and parsed (a clean
    negative counts as real and cacheable); ``False`` when ``value`` is the
    fail-closed substitution (call failed / index missing / FUP-skipped /
    deadline-skipped) and therefore MUST NOT be cached.
    """

    value: Any
    ok: bool


def _default_max_tokens_for(chunk: Sequence[Any]) -> int:
    """Output budget scales with chunk size (one verdict per item), bounded —
    mirrors the convergence judge's ``min(8000, 400 + 220 * len(chunk))``."""
    return min(8000, 400 + 220 * len(chunk))


async def batched_judge(
    items: Sequence[Any],
    *,
    build_prompt: Callable[[list[Any]], str],
    parse: Callable[[Any, dict[str, Any]], Any],
    fail_closed: Any,
    system: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    concurrency: int = 1,
    model_overrides: Optional[dict[str, Any]] = None,
    max_tokens_for: Optional[Callable[[list[Any]], int]] = None,
    call_llm: Optional[Callable[..., Awaitable[str]]] = None,
    parse_response: Optional[Callable[[str], dict[str, Any]]] = None,
    is_fup: Optional[Callable[[str], bool]] = None,
    deadline: Optional[float] = None,
    index_key: str = "index",
    stats: Optional[dict[str, Any]] = None,
) -> list[BatchedResult]:
    """Judge ``items`` in chunks of ``batch_size`` via one structured-output call
    each. See the module docstring for the ownership contract.

    ``fail_closed`` may be a value (used for every failed item) or a callable
    ``(item) -> value`` (e.g. a per-item heuristic fallback). ``parse(item,
    verdict_dict) -> result`` applies the caller's gate/transform to one verdict.
    ``model_overrides`` (e.g. ``{"model": ..., "base_url": ..., "api_key": ...}``)
    is threaded verbatim into ``call_llm`` so a site keeps its A/B model routing.
    """
    results: list[BatchedResult] = [
        BatchedResult(_resolve_fail_closed(fail_closed, item), ok=False) for item in items
    ]
    if not items:
        return results

    # Lazy imports keep this module free of import cycles and let callers inject
    # test doubles via the kwargs above.
    if call_llm is None or parse_response is None:
        from universal_agent.services.llm_classifier import (
            _call_llm as _default_call_llm,
            _parse_json_response as _default_parse_response,
        )

        call_llm = call_llm or _default_call_llm
        parse_response = parse_response or _default_parse_response
    if is_fup is None:
        from universal_agent.rate_limiter import _is_fup_error as _default_is_fup

        is_fup = _default_is_fup
    if max_tokens_for is None:
        max_tokens_for = _default_max_tokens_for

    overrides = dict(model_overrides or {})
    items_list = list(items)
    chunks: list[tuple[int, list[Any]]] = [
        (start, items_list[start : start + batch_size])
        for start in range(0, len(items_list), batch_size)
    ]

    _stats = stats if stats is not None else {}
    _stats.setdefault("calls_made", 0)
    _stats.setdefault("chunks_failed", 0)
    _stats.setdefault("skipped", 0)

    sem = asyncio.Semaphore(max(1, int(concurrency)))
    fup_tripped = False

    async def _judge_chunk(start: int, chunk: list[Any]) -> None:
        nonlocal fup_tripped
        async with sem:
            if fup_tripped:
                _stats["skipped"] += len(chunk)
                return  # leave fail-closed in place
            if deadline is not None and time.monotonic() >= deadline:
                _stats["skipped"] += len(chunk)
                return
            try:
                raw = await call_llm(
                    system=system or "",
                    user=build_prompt(chunk),
                    max_tokens=max_tokens_for(chunk),
                    **overrides,
                )
                parsed = parse_response(raw)
            except Exception as exc:  # noqa: BLE001
                # A Fair-Usage ([1313]) signal is account-level throttling —
                # re-raise the spirit of it by tripping the one-shot breaker so
                # the remaining chunks are skipped (re-detected next run) instead
                # of grinding out more doomed calls. Everything else fails closed
                # for this chunk only.
                if is_fup(str(exc)):
                    fup_tripped = True
                    logger.warning(
                        "batched_judge: Fair-Usage signal (chunk start=%d size=%d) — "
                        "tripping circuit breaker, skipping remaining chunks: %s",
                        start, len(chunk), exc,
                    )
                    _stats["chunks_failed"] += 1
                    return
                logger.warning(
                    "batched_judge: chunk failed closed (start=%d size=%d): %s",
                    start, len(chunk), exc,
                )
                _stats["chunks_failed"] += 1
                return
            _stats["calls_made"] += 1
            _apply_verdicts(
                results=results,
                start=start,
                chunk=chunk,
                parsed=parsed,
                parse=parse,
                index_key=index_key,
            )

    await asyncio.gather(*[_judge_chunk(start, chunk) for start, chunk in chunks])
    return results


def _resolve_fail_closed(fail_closed: Any, item: Any) -> Any:
    if callable(fail_closed):
        try:
            return fail_closed(item)
        except Exception as exc:  # noqa: BLE001
            logger.warning("batched_judge: fail_closed callable raised: %s", exc)
            return None
    return fail_closed


def _apply_verdicts(
    *,
    results: list[BatchedResult],
    start: int,
    chunk: list[Any],
    parsed: Any,
    parse: Callable[[Any, dict[str, Any]], Any],
    index_key: str,
) -> None:
    """Map a parsed ``{"verdicts": [...]}`` array back onto ``results`` by index.

    Items whose index is missing/out-of-range/non-int keep their fail-closed
    slot. Single-item chunks accept a bare verdict object (no ``verdicts`` array).
    """
    verdicts = parsed.get("verdicts") if isinstance(parsed, dict) else None
    if not isinstance(verdicts, list):
        # Single-item chunk: accept a bare verdict object as index 0. (The
        # production path uses ~20-item chunks and always gets the array.)
        if len(chunk) == 1 and isinstance(parsed, dict):
            verdicts = [{**parsed, index_key: 0}]
        else:
            logger.warning(
                "batched_judge: response had no 'verdicts' array (chunk start=%d "
                "size=%d) — failing closed for this chunk", start, len(chunk),
            )
            return
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        try:
            idx = int(v.get(index_key))
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(chunk)):
            continue
        item = chunk[idx]
        try:
            value = parse(item, v)
        except Exception as exc:  # noqa: BLE001
            # A caller `parse` that raises is a per-ITEM clean miss, not a chunk
            # failure — leave that one item fail-closed, keep the rest.
            logger.warning("batched_judge: parse() raised for item idx=%d: %s", idx, exc)
            continue
        results[start + idx] = BatchedResult(value, ok=True)
