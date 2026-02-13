#!/usr/bin/env python3
"""Fetch trending themes on X for a given query using Grok/xAI x_search."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict

from lib.xai_x_search import global_trends, parse_trends_response, trends_for_query


DEFAULT_MODEL = "grok-4-1-fast"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Discover trending themes on X for a query via xAI x_search.")
    p.add_argument("--query", help="Topic/query to search on X (required unless --global).")
    p.add_argument("--global", dest="global_mode", action="store_true", help="Infer broad/global trends (no query).")
    p.add_argument("--region", default="US", help="Region label to include in the prompt for --global (default: US).")
    p.add_argument("--from", dest="from_date", help="Start date YYYY-MM-DD (inclusive).")
    p.add_argument("--to", dest="to_date", help="End date YYYY-MM-DD (inclusive).")
    p.add_argument("--days", type=int, default=1, help="If --from/--to not set, use last N days (default: 1).")
    p.add_argument("--depth", choices=["quick", "default", "deep"], default="default", help="How many posts to pull.")
    p.add_argument("--max-themes", type=int, default=8, help="Max number of themes to infer.")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"xAI model to use (default: {DEFAULT_MODEL}).")
    p.add_argument("--json", action="store_true", help="Output JSON (themes + posts) only.")
    return p.parse_args(argv)


def _env_api_key() -> str | None:
    return os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY")


def _load_dotenv(dotenv_path: Path) -> None:
    """Minimal .env loader (stdlib only). Does not override existing env."""
    try:
        text = dotenv_path.read_text(encoding="utf-8")
    except Exception:
        return

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        key = k.strip()
        val = v.strip()
        if not key or key in os.environ:
            continue
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        os.environ[key] = val


def _maybe_load_project_env() -> None:
    # scripts/ -> grok-x-trends/ -> skills/ -> .claude/ -> repo-root/
    repo_root = Path(__file__).resolve().parents[4]
    dotenv_path = repo_root / ".env"
    _load_dotenv(dotenv_path)


def _compute_window(args: argparse.Namespace) -> tuple[str, str]:
    if args.from_date and args.to_date:
        return args.from_date, args.to_date

    # Inclusive day-level window (UTC). This is a limitation of current prompt + parsing.
    today = dt.datetime.utcnow().date()
    days = max(1, int(args.days))
    start = today - dt.timedelta(days=days)
    return start.isoformat(), today.isoformat()


def _print_markdown(result: Dict[str, Any], query: str, from_date: str, to_date: str) -> None:
    themes = result.get("themes", [])
    posts = result.get("posts", [])

    print(f"# X Trends for: {query}")
    print()
    print(f"- Window: {from_date} to {to_date}")
    print(f"- Themes: {len(themes)}")
    print(f"- Posts: {len(posts)}")
    print()

    if themes:
        print("## Themes")
        for t in themes:
            label = t.get("label", "")
            why = t.get("why_trending", "")
            kws = ", ".join(t.get("keywords", []) or [])
            print(f"- {label}")
            if kws:
                print(f"  - keywords: {kws}")
            if why:
                print(f"  - why: {why}")
            ex = t.get("example_urls", []) or []
            for u in ex[:3]:
                print(f"  - {u}")
        print()

    if posts:
        print("## Posts")
        for p in posts[:25]:
            url = p.get("url", "")
            handle = p.get("author_handle", "")
            text = (p.get("text") or "").replace("\n", " ").strip()
            if len(text) > 220:
                text = text[:217] + "..."
            print(f"- {url}")
            if handle:
                print(f"  - @{handle}")
            if text:
                print(f"  - {text}")


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    if not args.global_mode and not args.query:
        sys.stderr.write("error: --query is required unless --global is set\n")
        return 2

    _maybe_load_project_env()
    api_key = _env_api_key()
    if not api_key:
        sys.stderr.write("error: missing GROK_API_KEY (or XAI_API_KEY)\n")
        return 2

    from_date, to_date = _compute_window(args)

    if args.global_mode:
        raw = global_trends(
            api_key=api_key,
            model=args.model,
            region=args.region,
            from_date=from_date,
            to_date=to_date,
            depth=args.depth,
            max_themes=max(1, int(args.max_themes)),
        )
        query_label = f"GLOBAL ({args.region})"
    else:
        raw = trends_for_query(
            api_key=api_key,
            model=args.model,
            query=args.query,
            from_date=from_date,
            to_date=to_date,
            depth=args.depth,
            max_themes=max(1, int(args.max_themes)),
        )
        query_label = args.query
    parsed = parse_trends_response(raw)

    # If the model didn't follow the JSON-only contract, preserve raw_text for debugging.
    if args.json:
        out = {
            "query": query_label,
            "from_date": from_date,
            "to_date": to_date,
            "model": args.model,
            "depth": args.depth,
            "themes": parsed.get("themes", []),
            "posts": parsed.get("posts", []),
            "raw_text": parsed.get("raw_text", "") if (not parsed.get("themes") and not parsed.get("posts")) else "",
        }
        print(json.dumps(out, indent=2, ensure_ascii=True))
        return 0

    _print_markdown(parsed, query_label, from_date, to_date)
    if not parsed.get("themes") and not parsed.get("posts"):
        sys.stderr.write("warning: empty result; try --depth deep or broaden your query\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
