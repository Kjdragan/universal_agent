#!/usr/bin/env python3
"""Probe Threads API readiness for CSI adapters (owned + seeded + broad)."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.adapters.threads_api import ThreadsAPIClient, normalize_threads_item
from csi_ingester.config import load_config


def _emit(message: str, *, quiet: bool) -> None:
    if not quiet:
        print(message)


def _seed_terms_from_config(source_cfg: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    packs = source_cfg.get("query_packs") if isinstance(source_cfg.get("query_packs"), list) else []
    for pack in packs:
        if not isinstance(pack, dict):
            continue
        for item in pack.get("terms") if isinstance(pack.get("terms"), list) else []:
            cleaned = str(item or "").strip()
            if cleaned:
                terms.append(cleaned)
    for item in source_cfg.get("seed_terms") if isinstance(source_cfg.get("seed_terms"), list) else []:
        cleaned = str(item or "").strip()
        if cleaned:
            terms.append(cleaned)
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(term)
    return deduped


def _query_pool_from_config(source_cfg: dict[str, Any]) -> list[str]:
    pool = source_cfg.get("query_pool") if isinstance(source_cfg.get("query_pool"), list) else []
    out: list[str] = []
    seen: set[str] = set()
    for item in pool:
        cleaned = str(item or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


async def _probe_owned(source_cfg: dict[str, Any], *, limit: int, quiet: bool) -> dict[str, Any]:
    client = ThreadsAPIClient.from_config(source_cfg, quota_state={})
    if not client.is_configured():
        _emit("THREADS_PROBE_OWNED_SKIP reason=missing_credentials", quiet=quiet)
        return {"ok": 0, "fail": 1, "status": "skip", "reason": "missing_credentials", "preview_ids": []}
    try:
        posts = await client.get_user_threads(limit=limit)
    except Exception as exc:
        _emit(f"THREADS_PROBE_OWNED_FAIL error={type(exc).__name__}:{exc}", quiet=quiet)
        return {"ok": 0, "fail": 1, "status": "fail", "reason": f"{type(exc).__name__}:{exc}", "preview_ids": []}

    preview = []
    for row in posts[: min(3, len(posts))]:
        normalized = normalize_threads_item(row)
        preview.append(str(normalized.get("media_id") or ""))
    _emit(f"THREADS_PROBE_OWNED_OK posts={len(posts)} preview_ids={preview}", quiet=quiet)

    mentions_count = None
    try:
        mentions = await client.get_mentions(limit=min(10, limit))
        mentions_count = len(mentions)
        _emit(f"THREADS_PROBE_OWNED_MENTIONS_OK mentions={len(mentions)}", quiet=quiet)
    except Exception as exc:
        _emit(f"THREADS_PROBE_OWNED_MENTIONS_WARN error={type(exc).__name__}:{exc}", quiet=quiet)

    return {
        "ok": 1,
        "fail": 0,
        "status": "ok",
        "posts": len(posts),
        "mentions": mentions_count,
        "preview_ids": preview,
    }


async def _probe_seeded(
    source_cfg: dict[str, Any],
    *,
    limit: int,
    max_terms: int,
    override_term: str,
    quiet: bool,
) -> dict[str, Any]:
    client = ThreadsAPIClient.from_config(source_cfg, quota_state={})
    if not client.is_configured():
        _emit("THREADS_PROBE_SEEDED_SKIP reason=missing_credentials", quiet=quiet)
        return {"ok": 0, "fail": 1, "status": "skip", "reason": "missing_credentials", "terms": []}

    terms = [override_term.strip()] if override_term.strip() else _seed_terms_from_config(source_cfg)
    if not terms:
        _emit("THREADS_PROBE_SEEDED_SKIP reason=no_seed_terms", quiet=quiet)
        return {"ok": 0, "fail": 1, "status": "skip", "reason": "no_seed_terms", "terms": []}

    ok = 0
    fail = 0
    term_results: list[dict[str, Any]] = []
    for term in terms[: max(1, max_terms)]:
        try:
            rows = await client.keyword_search(query=term, search_type="TOP", limit=limit)
        except Exception as exc:
            fail += 1
            _emit(f"THREADS_PROBE_SEEDED_FAIL term={term} error={type(exc).__name__}:{exc}", quiet=quiet)
            term_results.append(
                {
                    "term": term,
                    "status": "fail",
                    "error": f"{type(exc).__name__}:{exc}",
                    "results": 0,
                    "preview_ids": [],
                }
            )
            continue
        ok += 1
        preview = [str(normalize_threads_item(row, term=term).get("media_id") or "") for row in rows[:3]]
        _emit(f"THREADS_PROBE_SEEDED_OK term={term} results={len(rows)} preview_ids={preview}", quiet=quiet)
        term_results.append(
            {"term": term, "status": "ok", "results": len(rows), "preview_ids": preview}
        )
    return {
        "ok": ok,
        "fail": fail,
        "status": "ok" if ok > 0 and fail == 0 else ("partial" if ok > 0 else "fail"),
        "terms": term_results,
    }


async def _probe_broad(source_cfg: dict[str, Any], *, limit: int, max_terms: int, quiet: bool) -> dict[str, Any]:
    client = ThreadsAPIClient.from_config(source_cfg, quota_state={})
    if not client.is_configured():
        _emit("THREADS_PROBE_BROAD_SKIP reason=missing_credentials", quiet=quiet)
        return {"ok": 0, "fail": 1, "status": "skip", "reason": "missing_credentials", "queries": []}

    queries = _query_pool_from_config(source_cfg)
    if not queries:
        _emit("THREADS_PROBE_BROAD_SKIP reason=no_query_pool", quiet=quiet)
        return {"ok": 0, "fail": 1, "status": "skip", "reason": "no_query_pool", "queries": []}

    ok = 0
    fail = 0
    query_results: list[dict[str, Any]] = []
    for query in queries[: max(1, max_terms)]:
        try:
            rows = await client.keyword_search(query=query, search_type="TOP", limit=limit)
        except Exception as exc:
            fail += 1
            _emit(f"THREADS_PROBE_BROAD_FAIL query={query} error={type(exc).__name__}:{exc}", quiet=quiet)
            query_results.append(
                {
                    "query": query,
                    "status": "fail",
                    "error": f"{type(exc).__name__}:{exc}",
                    "results": 0,
                    "preview_ids": [],
                }
            )
            continue
        ok += 1
        preview = [str(normalize_threads_item(row, term=query).get("media_id") or "") for row in rows[:3]]
        _emit(f"THREADS_PROBE_BROAD_OK query={query} results={len(rows)} preview_ids={preview}", quiet=quiet)
        query_results.append(
            {"query": query, "status": "ok", "results": len(rows), "preview_ids": preview}
        )
    return {
        "ok": ok,
        "fail": fail,
        "status": "ok" if ok > 0 and fail == 0 else ("partial" if ok > 0 else "fail"),
        "queries": query_results,
    }


async def _main_async() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", default="config/config.yaml")
    parser.add_argument("--source", default="all", choices=["all", "owned", "seeded", "broad"])
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-terms", type=int, default=3)
    parser.add_argument("--seed-term", default="", help="Override single seeded term for probe")
    parser.add_argument("--require-all", action="store_true", help="Fail unless all requested probe classes pass")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print machine-readable JSON summary")
    parser.add_argument("--quiet", action="store_true", help="Suppress line-by-line probe output")
    args = parser.parse_args()

    cfg = load_config(args.config_path)
    sources = cfg.raw.get("sources") if isinstance(cfg.raw, dict) else {}
    if not isinstance(sources, dict):
        _emit("THREADS_PROBE_FAIL reason=invalid_config", quiet=args.quiet)
        if args.as_json:
            print(json.dumps({"ok_count": 0, "fail_count": 1, "error": "invalid_config"}, sort_keys=True))
        return 2

    owned_cfg = sources.get("threads_owned") if isinstance(sources.get("threads_owned"), dict) else {}
    seeded_cfg = sources.get("threads_trends_seeded") if isinstance(sources.get("threads_trends_seeded"), dict) else {}
    broad_cfg = sources.get("threads_trends_broad") if isinstance(sources.get("threads_trends_broad"), dict) else {}

    ok_total = 0
    fail_total = 0
    requested_sources: list[str] = []
    source_results: dict[str, dict[str, Any]] = {}

    if args.source in {"all", "owned"}:
        requested_sources.append("owned")
        result = await _probe_owned(owned_cfg, limit=max(1, min(20, int(args.limit))), quiet=args.quiet)
        source_results["owned"] = result
        ok_total += int(result.get("ok") or 0)
        fail_total += int(result.get("fail") or 0)

    if args.source in {"all", "seeded"}:
        requested_sources.append("seeded")
        result = await _probe_seeded(
            seeded_cfg,
            limit=max(1, min(20, int(args.limit))),
            max_terms=max(1, min(20, int(args.max_terms))),
            override_term=str(args.seed_term or ""),
            quiet=args.quiet,
        )
        source_results["seeded"] = result
        ok_total += int(result.get("ok") or 0)
        fail_total += int(result.get("fail") or 0)

    if args.source in {"all", "broad"}:
        requested_sources.append("broad")
        result = await _probe_broad(
            broad_cfg,
            limit=max(1, min(20, int(args.limit))),
            max_terms=max(1, min(20, int(args.max_terms))),
            quiet=args.quiet,
        )
        source_results["broad"] = result
        ok_total += int(result.get("ok") or 0)
        fail_total += int(result.get("fail") or 0)

    if args.require_all:
        all_passed = True
        for source_name in requested_sources:
            result = source_results.get(source_name) or {}
            status = str(result.get("status") or "").strip().lower()
            ok_count = int(result.get("ok") or 0)
            if ok_count <= 0 or status in {"skip", "fail"}:
                all_passed = False
                break
    else:
        all_passed = ok_total > 0

    _emit(f"THREADS_PROBE_OK_COUNT={ok_total}", quiet=args.quiet)
    _emit(f"THREADS_PROBE_FAIL_COUNT={fail_total}", quiet=args.quiet)
    if args.require_all:
        _emit(f"THREADS_PROBE_REQUIRE_ALL={1}", quiet=args.quiet)
        _emit(f"THREADS_PROBE_ALL_PASSED={1 if all_passed else 0}", quiet=args.quiet)

    if args.as_json:
        print(
            json.dumps(
                {
                    "requested_sources": requested_sources,
                    "ok_count": ok_total,
                    "fail_count": fail_total,
                    "require_all": bool(args.require_all),
                    "all_passed": bool(all_passed),
                    "source_results": source_results,
                },
                sort_keys=True,
            )
        )

    return 0 if all_passed else 1


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
