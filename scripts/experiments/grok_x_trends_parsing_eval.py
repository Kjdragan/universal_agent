#!/usr/bin/env python3
"""
Standalone evaluation harness for grok-x-trends parsing approaches.

Goal: Compare "direct JSON parse" vs "regex extraction" behavior on fixtures,
so we can choose the safest default for downstream structured evaluation.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Tuple


def extract_output_text(resp: Dict[str, Any]) -> str:
    out = resp.get("output")
    if isinstance(out, list):
        for item in out:
            if isinstance(item, dict) and item.get("type") == "message":
                for c in item.get("content", []) or []:
                    if isinstance(c, dict) and c.get("type") == "output_text":
                        return str(c.get("text") or "")
    return ""


def parse_direct_json(text: str) -> Tuple[bool, str]:
    try:
        json.loads(text)
        return True, "ok"
    except Exception as e:
        return False, type(e).__name__


def parse_regex_embedded(text: str) -> Tuple[bool, str]:
    m = re.search(r'\{[\s\S]*"posts"[\s\S]*\}', text)
    if not m:
        return False, "no_match"
    try:
        json.loads(m.group(0))
        return True, "ok"
    except Exception as e:
        return False, type(e).__name__


def main() -> int:
    fixtures_dir = Path("tests/fixtures/grok_x_trends")
    paths = sorted(fixtures_dir.glob("*.json"))
    if not paths:
        print("no fixtures found under tests/fixtures/grok_x_trends", file=sys.stderr)
        return 2

    print(f"fixtures={len(paths)}")
    for p in paths:
        resp = json.loads(p.read_text(encoding="utf-8"))
        text = extract_output_text(resp).strip()
        direct_ok, direct_msg = parse_direct_json(text)
        regex_ok, regex_msg = parse_regex_embedded(text)
        print(f"- {p.name}: direct={direct_ok}({direct_msg}) regex={regex_ok}({regex_msg}) first_char={text[:1]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

