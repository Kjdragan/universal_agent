#!/usr/bin/env python3
"""Probe Threads API readiness for CSI adapters (owned + seeded + broad)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from csi_ingester.adapters.threads_api import ThreadsAPIClient, normalize_threads_item
from csi_ingester.config import load_config


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


async def _probe_owned(source_cfg: dict[str, Any], *, limit: int) -> tuple[int, int]:
    client = ThreadsAPIClient.from_config(source_cfg, quota_state={})
    if not client.is_configured():
        print("THREADS_PROBE_OWNED_SKIP reason=missing_credentials")
        return 0, 1
    try:
        posts = await client.get_user_threads(limit=limit)
    except Exception as exc:
        print(f"THREADS_PROBE_OWNED_FAIL error={type(exc).__name__}:{exc}")
        return 0, 1

    preview = []
    for row in posts[: min(3, len(posts))]:
        normalized = normalize_threads_item(row)
        preview.append(str(normalized.get("media_id") or ""))
    print(f"THREADS_PROBE_OWNED_OK posts={len(posts)} preview_ids={preview}")

    try:
        mentions = await client.get_mentions(limit=min(10, limit))
        print(f"THREADS_PROBE_OWNED_MENTIONS_OK mentions={len(mentions)}")
    except Exception as exc:
        print(f"THREADS_PROBE_OWNED_MENTIONS_WARN error={type(exc).__name__}:{exc}")

    return 1, 0


async def _probe_seeded(source_cfg: dict[str, Any], *, limit: int, max_terms: int, override_term: str) -> tuple[int, int]:
    client = ThreadsAPIClient.from_config(source_cfg, quota_state={})
    if not client.is_configured():
        print("THREADS_PROBE_SEEDED_SKIP reason=missing_credentials")
        return 0, 1

    terms = [override_term.strip()] if override_term.strip() else _seed_terms_from_config(source_cfg)
    if not terms:
        print("THREADS_PROBE_SEEDED_SKIP reason=no_seed_terms")
        return 0, 1

    ok = 0
    fail = 0
    for term in terms[: max(1, max_terms)]:
        try:
            rows = await client.keyword_search(query=term, search_type="TOP", limit=limit)
        except Exception as exc:
            fail += 1
            print(f"THREADS_PROBE_SEEDED_FAIL term={term} error={type(exc).__name__}:{exc}")
            continue
        ok += 1
        preview = [str(normalize_threads_item(row, term=term).get("media_id") or "") for row in rows[:3]]
        print(f"THREADS_PROBE_SEEDED_OK term={term} results={len(rows)} preview_ids={preview}")
    return ok, fail


async def _probe_broad(source_cfg: dict[str, Any], *, limit: int, max_terms: int) -> tuple[int, int]:
    client = ThreadsAPIClient.from_config(source_cfg, quota_state={})
    if not client.is_configured():
        print("THREADS_PROBE_BROAD_SKIP reason=missing_credentials")
        return 0, 1

    queries = _query_pool_from_config(source_cfg)
    if not queries:
        print("THREADS_PROBE_BROAD_SKIP reason=no_query_pool")
        return 0, 1

    ok = 0
    fail = 0
    for query in queries[: max(1, max_terms)]:
        try:
            rows = await client.keyword_search(query=query, search_type="TOP", limit=limit)
        except Exception as exc:
            fail += 1
            print(f"THREADS_PROBE_BROAD_FAIL query={query} error={type(exc).__name__}:{exc}")
            continue
        ok += 1
        preview = [str(normalize_threads_item(row, term=query).get("media_id") or "") for row in rows[:3]]
        print(f"THREADS_PROBE_BROAD_OK query={query} results={len(rows)} preview_ids={preview}")
    return ok, fail


async def _main_async() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-path", default="config/config.yaml")
    parser.add_argument("--source", default="all", choices=["all", "owned", "seeded", "broad"])
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-terms", type=int, default=3)
    parser.add_argument("--seed-term", default="", help="Override single seeded term for probe")
    args = parser.parse_args()

    cfg = load_config(args.config_path)
    sources = cfg.raw.get("sources") if isinstance(cfg.raw, dict) else {}
    if not isinstance(sources, dict):
        print("THREADS_PROBE_FAIL reason=invalid_config")
        return 2

    owned_cfg = sources.get("threads_owned") if isinstance(sources.get("threads_owned"), dict) else {}
    seeded_cfg = sources.get("threads_trends_seeded") if isinstance(sources.get("threads_trends_seeded"), dict) else {}
    broad_cfg = sources.get("threads_trends_broad") if isinstance(sources.get("threads_trends_broad"), dict) else {}

    ok_total = 0
    fail_total = 0

    if args.source in {"all", "owned"}:
        ok, fail = await _probe_owned(owned_cfg, limit=max(1, min(20, int(args.limit))))
        ok_total += ok
        fail_total += fail

    if args.source in {"all", "seeded"}:
        ok, fail = await _probe_seeded(
            seeded_cfg,
            limit=max(1, min(20, int(args.limit))),
            max_terms=max(1, min(20, int(args.max_terms))),
            override_term=str(args.seed_term or ""),
        )
        ok_total += ok
        fail_total += fail

    if args.source in {"all", "broad"}:
        ok, fail = await _probe_broad(
            broad_cfg,
            limit=max(1, min(20, int(args.limit))),
            max_terms=max(1, min(20, int(args.max_terms))),
        )
        ok_total += ok
        fail_total += fail

    print(f"THREADS_PROBE_OK_COUNT={ok_total}")
    print(f"THREADS_PROBE_FAIL_COUNT={fail_total}")
    return 0 if ok_total > 0 else 1


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
