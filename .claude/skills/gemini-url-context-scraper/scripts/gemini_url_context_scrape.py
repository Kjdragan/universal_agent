# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "google-genai>=1.0.0",
#   "python-dotenv>=1.0.1",
#   "requests>=2.31.0",
# ]
# ///

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from google import genai
from google.genai import types
import requests


load_dotenv(find_dotenv(usecwd=True))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _repo_root_from_here() -> Path:
    # Walk upward from this script location until we find a likely repo marker.
    cur = Path(__file__).resolve()
    for parent in [cur.parent, *cur.parents]:
        if (parent / ".git").exists():
            return parent
        if (parent / "pyproject.toml").exists():
            return parent
    # Fallback: current working directory.
    return Path.cwd().resolve()


def _safe_slug_component(value: str) -> str:
    value = (value or "").strip().lower()
    out: list[str] = []
    last_sep = False
    for ch in value:
        is_alnum = ("a" <= ch <= "z") or ("0" <= ch <= "9")
        if is_alnum:
            out.append(ch)
            last_sep = False
        else:
            if not last_sep:
                out.append("-")
                last_sep = True
    slug = "".join(out).strip("-")
    return slug or "run"


def _default_slug(urls: list[str]) -> str:
    if not urls:
        return "gemini-url-context"
    # Use domain + last path component.
    first = urls[0]
    m = re.match(r"^https?://([^/]+)(/.*)?$", first.strip())
    if not m:
        return _safe_slug_component(first)[:48] or "gemini-url-context"
    host = _safe_slug_component(m.group(1))
    path = (m.group(2) or "").strip("/")
    tail = _safe_slug_component(path.split("/")[-1]) if path else ""
    base = host if not tail else f"{host}-{tail}"
    return (base[:56] or "gemini-url-context").strip("-")


def _resolve_artifacts_root() -> Path:
    raw = (os.environ.get("UA_ARTIFACTS_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_repo_root_from_here() / "artifacts").resolve()


def _resolve_session_workspace() -> Path | None:
    raw = (os.environ.get("CURRENT_SESSION_WORKSPACE") or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class RunPaths:
    run_dir: Path
    answer_md: Path
    manifest_json: Path
    readme_md: Path | None


def _build_run_paths(*, persist: bool, slug: str) -> RunPaths:
    now = _utc_now()
    hhmmss = now.strftime("%H%M%S")
    date = now.strftime("%Y-%m-%d")
    safe_slug = _safe_slug_component(slug)

    if persist:
        root = _resolve_artifacts_root()
        run_dir = root / "gemini-url-context" / date / f"{safe_slug}__{hhmmss}"
        readme = run_dir / "README.md"
    else:
        ws = _resolve_session_workspace()
        if ws is None:
            raise RuntimeError(
                "CURRENT_SESSION_WORKSPACE is not set; cannot write interim work_products. "
                "Run via UA (gateway/CLI) or re-run with --persist."
            )
        run_dir = ws / "work_products" / "gemini-url-context" / f"{safe_slug}__{hhmmss}"
        readme = None

    return RunPaths(
        run_dir=run_dir,
        answer_md=run_dir / "answer.md",
        manifest_json=run_dir / "manifest.json",
        readme_md=readme,
    )


def _api_key() -> str | None:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def _build_client(require_key: bool) -> genai.Client:
    key = _api_key()
    if not key:
        if require_key:
            raise RuntimeError(
                "Missing GOOGLE_API_KEY (or GEMINI_API_KEY). Put it in your .env or export it.\n"
                "Key source: https://aistudio.google.com/app/apikey"
            )
        # Self-test path: allow client construction without key.
        return genai.Client(api_key="test")
    return genai.Client(api_key=key)


def _build_prompt(*, urls: list[str], question: str, mode: str) -> str:
    if len(urls) == 1:
        url_section = f"URL: {urls[0]}"
    else:
        url_section = "URLs:\n" + "\n".join(f"- {u}" for u in urls)

    mode_hint = ""
    if mode == "extract":
        mode_hint = (
            "\n\nExtraction mode:\n"
            "- Extract the relevant content from the provided URL(s).\n"
            "- Preserve structure (headings, bullets, tables where possible).\n"
            "- Include citations/links where available.\n"
        )
    elif mode == "summary":
        mode_hint = (
            "\n\nSummary mode:\n"
            "- Summarize the key points and include any important details.\n"
            "- Include citations/links where available.\n"
        )

    return f"{url_section}\n\nQuestion: {question}{mode_hint}"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def self_test() -> int:
    try:
        # Verify imports + tool construction (no API calls).
        _ = types.GenerateContentConfig(tools=[types.Tool(url_context=types.UrlContext())])
        _build_client(require_key=False)
        print("SELF_TEST_OK")
        return 0
    except Exception as e:
        print(f"SELF_TEST_FAILED: {e}", file=sys.stderr)
        return 1


def _maybe_generate_with_url_context(*, client: genai.Client, model: str, prompt: str) -> tuple[str | None, str | None]:
    """
    Try URL Context tool first. Some API surfaces reject it with:
      'Browse tool is not supported.'

    Returns (text, error). If text is None, error is non-empty.
    """
    try:
        config = types.GenerateContentConfig(tools=[types.Tool(url_context=types.UrlContext())])
        resp = client.models.generate_content(model=model, contents=prompt, config=config)
        return (resp.text or "").strip(), None
    except Exception as e:
        return None, str(e)


def _guess_mime(url: str) -> str:
    lower = url.lower()
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "text/plain"


def _download_bytes(url: str, *, max_bytes: int) -> bytes:
    resp = requests.get(url, stream=True, timeout=30)
    resp.raise_for_status()
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=1024 * 64):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(f"Download exceeded max_bytes={max_bytes}: {url}")
        chunks.append(chunk)
    return b"".join(chunks)


def _generate_with_downloaded_inputs(
    *, client: genai.Client, model: str, urls: list[str], question: str, mode: str, max_download_bytes: int
) -> str:
    """
    Fallback path when URL Context tool isn't available:
    - Download each URL and attach it as inline content (PDF/image as bytes, text/HTML as string).
    - Ask the model to answer based on the attached content.
    """
    contents: list[Any] = []
    for url in urls:
        mime = _guess_mime(url)
        if mime.startswith("image/") or mime == "application/pdf":
            data = _download_bytes(url, max_bytes=max_download_bytes)
            contents.append(types.Part.from_bytes(data=data, mime_type=mime))
            contents.append(f"Source URL: {url}")
        else:
            # Treat as text/HTML. This is lossy but works without browse tools.
            data = _download_bytes(url, max_bytes=min(max_download_bytes, 2_000_000))
            text = data.decode("utf-8", errors="replace")
            # Keep the prompt size bounded (Gemini will still see the key parts).
            text = text[:200_000]
            contents.append(f"Source URL: {url}\n\nCONTENT_START\n{text}\nCONTENT_END")

    prompt = _build_prompt(urls=urls, question=question, mode=mode)
    contents.append(prompt)
    resp = client.models.generate_content(model=model, contents=contents)
    return (resp.text or "").strip()


def run(*, urls: list[str], question: str, mode: str, model: str, persist: bool, slug: str) -> int:
    paths = _build_run_paths(persist=persist, slug=slug)
    paths.run_dir.mkdir(parents=True, exist_ok=True)

    client = _build_client(require_key=True)
    prompt = _build_prompt(urls=urls, question=question, mode=mode)

    extraction: dict[str, Any] = {"url_context": "not_attempted", "download_fallback": "not_attempted"}
    answer: str | None = None

    text, err = _maybe_generate_with_url_context(client=client, model=model, prompt=prompt)
    if text:
        extraction["url_context"] = "attempted_succeeded"
        answer = text
    else:
        extraction["url_context"] = "attempted_failed"
        extraction["url_context_error"] = (err or "")[:1000]
        # Known failure mode: Gemini Developer API sometimes rejects URL context ("Browse tool").
        # Fallback: download inputs and attach as file/text parts.
        try:
            extraction["download_fallback"] = "attempting"
            answer = _generate_with_downloaded_inputs(
                client=client,
                model=model,
                urls=urls,
                question=question,
                mode=mode,
                max_download_bytes=10_000_000,
            )
            extraction["download_fallback"] = "attempted_succeeded"
        except Exception as e:
            extraction["download_fallback"] = "attempted_failed"
            extraction["download_fallback_error"] = str(e)[:1000]
            raise

    if not answer:
        answer = "(Empty response text)"

    _write_text(paths.answer_md, answer + "\n")

    manifest: dict[str, Any] = {
        "artifact_type": "gemini_url_context_scrape",
        "created_at": _utc_now().isoformat(),
        "persist": bool(persist),
        "model": model,
        "mode": mode,
        "extraction": extraction,
        "inputs": {"urls": urls, "question": question},
        # Retention map is primarily used for persistent artifacts (UA_ARTIFACTS_DIR).
        # For interim outputs under CURRENT_SESSION_WORKSPACE, everything is effectively ephemeral.
        "retention": (
            {"default": "keep", "answer.md": "keep", "manifest.json": "keep", "README.md": "keep"}
            if persist
            else {"default": "temp", "answer.md": "temp", "manifest.json": "temp"}
        ),
        "outputs": {
            "answer.md": "keep" if persist else "temp",
            "manifest.json": "keep" if persist else "temp",
            "README.md": "keep" if persist else "not_created",
        },
    }
    _write_text(paths.manifest_json, json.dumps(manifest, indent=2) + "\n")

    if paths.readme_md is not None:
        rel = paths.answer_md.relative_to(paths.run_dir)
        _write_text(
            paths.readme_md,
            (
                "# Gemini URL Context Scrape\n\n"
                "This is a persistent artifact run produced by `gemini-url-context-scraper`.\n\n"
                "## Files\n"
                f"- `{rel}`: grounded answer/extraction\n"
                "- `manifest.json`: inputs + retention\n\n"
                "## Rerun\n\n"
                "```bash\n"
                f"uv run .claude/skills/gemini-url-context-scraper/scripts/gemini_url_context_scrape.py "
                + " \\\n"
                + "\n".join([f'  --url \"{u}\" \\' for u in urls])
                + f"\n  --question {json.dumps(question)} \\\n"
                + f"  --mode {mode} \\\n"
                + f"  --model {model} \\\n"
                + "  --persist\n"
                "```\n"
            ),
        )

    # Print paths for downstream use.
    print(str(paths.answer_md))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape URLs/PDFs/images using Gemini URL Context.")
    ap.add_argument("--url", action="append", dest="urls", default=[], help="Public URL (repeatable).")
    ap.add_argument("--question", required=False, default="Summarize the key points.", help="Prompt/question.")
    ap.add_argument("--mode", choices=["summary", "extract"], default="summary", help="Output guidance mode.")
    ap.add_argument("--model", default="gemini-2.5-flash", help="Gemini model id (default: gemini-2.5-flash).")
    ap.add_argument("--persist", action="store_true", help="Write durable outputs under UA_ARTIFACTS_DIR.")
    ap.add_argument("--slug", default="", help="Run folder slug (default derived from URL).")
    ap.add_argument("--self-test", action="store_true", help="Validate imports/tool construction (no API call).")

    args = ap.parse_args()
    if args.self_test:
        return self_test()

    if not args.urls:
        ap.error("--url is required (repeatable).")

    slug = args.slug.strip() or _default_slug(args.urls)
    return run(
        urls=args.urls,
        question=args.question,
        mode=args.mode,
        model=args.model,
        persist=bool(args.persist),
        slug=slug,
    )


if __name__ == "__main__":
    raise SystemExit(main())
