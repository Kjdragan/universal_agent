#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["google-genai>=1.0.0", "python-dotenv>=1.0.1"]
# ///

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from google import genai


DEFAULT_PROMPT = (
    "Analyze this video and provide: "
    "1) a concise summary, "
    "2) timestamped key moments (MM:SS), "
    "3) notable visual-only observations, "
    "4) implementation-relevant takeaways."
)


def _api_key() -> str | None:
    return (
        os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GENAI_API_KEY")
    )


def _extract_text(response: object) -> str:
    text = str(getattr(response, "text", "") or "").strip()
    if text:
        return text

    chunks: list[str] = []
    candidates = getattr(response, "candidates", None)
    if isinstance(candidates, list):
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None)
            if not isinstance(parts, list):
                continue
            for part in parts:
                part_text = str(getattr(part, "text", "") or "").strip()
                if part_text:
                    chunks.append(part_text)
    return "\n\n".join(chunks).strip()


def _write_text(path: str, text: str) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def self_test() -> int:
    if genai.Client is None:
        print("SELF_TEST_FAIL: google-genai import failed", file=sys.stderr)
        return 1
    print("SELF_TEST_OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gemini multimodal analysis for a public YouTube URL.")
    parser.add_argument("--self-test", action="store_true", help="Run no-network dependency/import checks.")
    parser.add_argument("--url", help="Public YouTube URL to analyze.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Analysis prompt.")
    parser.add_argument("--model", default="gemini-3-pro-preview", help="Gemini model name.")
    parser.add_argument("--out", help="Optional markdown output path.")
    parser.add_argument("--json-out", help="Optional JSON output path.")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    load_dotenv(find_dotenv(usecwd=True))

    if not args.url:
        print("Missing required --url", file=sys.stderr)
        return 2

    api_key = _api_key()
    if not api_key:
        print("Missing GOOGLE_API_KEY or GEMINI_API_KEY", file=sys.stderr)
        return 3

    client = genai.Client(api_key=api_key)
    contents = [
        {
            "role": "user",
            "parts": [
                {"file_data": {"file_uri": args.url}},
                {"text": args.prompt},
            ],
        }
    ]
    response = client.models.generate_content(
        model=args.model,
        contents=contents,
    )
    text = _extract_text(response)

    if not text:
        print("Gemini returned empty response text", file=sys.stderr)
        return 4

    if args.out:
        _write_text(args.out, text + "\n")
    else:
        print(text)

    if args.json_out:
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "model": args.model,
            "url": args.url,
            "prompt": args.prompt,
            "text": text,
        }
        _write_text(args.json_out, json.dumps(payload, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
