#!/usr/bin/env python3
"""Durably archive a scratchpad artifact into a per-project, dated archive + index.

WHY THIS EXISTS
    Every scratchpad publish (``publish_scratch.sh``) copies the artifact into the live
    tailnet serve store (``/home/ua/ua_scratch/<slug>/``) so it gets a URL. That store is
    flat (one dir per slug, no project grouping, no dates) and is the *only* copy — there
    is no standing, organized, durable record of "every exhibit we ever made". This script
    adds exactly that: on each publish it drops a second, permanent, **dated + named** copy
    into a per-project archive root and appends it to an ongoing index, so the operator
    always has a browsable file of all artifacts, independent of the docs system.

    It is invoked best-effort by ``publish_scratch.sh`` *after* a successful publish, on the
    same machine that published — so the archive lands where that machine's durable store
    is: a git-tracked ``<repo>/scratch_archive/`` for interactive desktop work, or the
    permanent ``/home/ua/ua_scratch_archive/`` for autonomous VPS runs. The script auto-
    detects nothing itself; the caller passes the resolved ``--root``.

    Stdlib-only on purpose (mirrors ``build_scratch_index.py``): it runs on the desktop and
    the VPS with no venv. Failures must never break a publish — the artifact is already
    served — so the caller suppresses our exit code.

LAYOUT (under ``--root``)
    <root>/index.jsonl                      append-only ledger (source of truth)
    <root>/INDEX.md                         human-readable, newest-first (git-friendly)
    <root>/index.html                       browsable, searchable (served on the VPS)
    <root>/<YYYY-MM-DD>/<HHMMSS>__<slug>__<name>          a single-file artifact
    <root>/<YYYY-MM-DD>/<HHMMSS>__<slug>/<tree...>        a docset (directory) artifact

USAGE
    scratch_archive.py --src PATH --slug SLUG --root DIR [--dir] [--url URL]
"""

from __future__ import annotations

import argparse
from datetime import datetime
import html as _html
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys

logger = logging.getLogger(__name__)

LEDGER_NAME = "index.jsonl"
MD_INDEX_NAME = "INDEX.md"
HTML_INDEX_NAME = "index.html"
LOCK_NAME = ".index.lock"
SIDECAR_NAME = "_artifact.json"  # written by scratch_publish.py for metadata publishes

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_HUB_PREFERENCE = ("index.html", "design.html", "readme.html", "index.htm")


# --------------------------------------------------------------------------------------
# Title derivation — rich when a sidecar/HTML/markdown gives one, else the slug.
# --------------------------------------------------------------------------------------
def _title_from_html(text: str) -> str | None:
    m = _TITLE_RE.search(text)
    return _html.unescape(m.group(1)).strip() if m else None


def _title_from_markdown(text: str) -> str | None:
    m = _H1_RE.search(text)
    return re.sub(r"[*_`]", "", m.group(1)).strip() if m else None


def _derive_title(path: Path, slug: str) -> str:
    """Best-effort human title for a single file; fall back to the slug."""
    try:
        suffix = path.suffix.lower()
        if suffix in {".html", ".htm"}:
            t = _title_from_html(path.read_text(encoding="utf-8", errors="replace"))
            if t:
                return t
        elif suffix in {".md", ".markdown", ".mdown", ".mkd"}:
            t = _title_from_markdown(path.read_text(encoding="utf-8", errors="replace"))
            if t:
                return t
    except OSError:
        pass
    return slug


def _docset_title_and_entry(src_dir: Path, slug: str) -> tuple[str, str]:
    """Title + landing-file (relative to the archived dir) for a docset.

    Prefers the ``_artifact.json`` sidecar that ``scratch_publish.py`` writes for metadata
    publishes; else picks a conventional hub page; else the first file. Returns
    ``(title, entry_relpath)`` where ``entry_relpath`` is "" if nothing renderable.
    """
    sidecar = src_dir / SIDECAR_NAME
    if sidecar.is_file():
        try:
            rec = json.loads(sidecar.read_text(encoding="utf-8"))
            title = (rec.get("title") or "").strip()
            entry = (rec.get("entry") or "").strip()
            if title:
                return title, entry
        except (OSError, ValueError):
            pass

    files = [p for p in sorted(src_dir.rglob("*")) if p.is_file() and p.name != SIDECAR_NAME]
    htmls = [p for p in files if p.suffix.lower() in {".html", ".htm"}]
    hub: Path | None = None
    for pref in _HUB_PREFERENCE:
        for p in htmls:  # path-sorted, so top-level beats nested
            if p.name.lower() == pref:
                hub = p
                break
        if hub:
            break
    if hub is None and htmls:
        hub = htmls[0]
    if hub is not None:
        entry = hub.relative_to(src_dir).as_posix()
        title = _derive_title(hub, slug)
        return title, entry
    return slug, (files[0].relative_to(src_dir).as_posix() if files else "")


# --------------------------------------------------------------------------------------
# Cross-process lock (the VPS archive is appended to by concurrent autonomous publishes).
# --------------------------------------------------------------------------------------
class _Lock:
    def __init__(self, path: Path):
        self._path = path
        self._fh = None

    def __enter__(self):
        try:
            import fcntl  # noqa: PLC0415 — POSIX-only; absence degrades to no-lock

            self._fh = open(self._path, "w", encoding="utf-8")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        except Exception:  # noqa: BLE001 — locking is best-effort hardening, never fatal
            self._fh = None
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            try:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
            self._fh.close()
            self._fh = None
        return False


# --------------------------------------------------------------------------------------
# Index rendering (rebuilt from the ledger on every publish; cheap and idempotent).
# --------------------------------------------------------------------------------------
def _read_ledger(root: Path) -> list[dict]:
    path = root / LEDGER_NAME
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _render_markdown_index(root: Path, records: list[dict]) -> str:
    title = root.name or "scratchpad"
    lines = [
        f"# Scratchpad artifact archive — `{title}`",
        "",
        "> Durable, dated copy of every artifact published to the tailnet scratchpad.",
        "> Newest first. Auto-generated from `index.jsonl` — do not edit by hand.",
        f"> {len(records)} artifact(s) archived.",
        "",
    ]
    if not records:
        lines.append("_No artifacts archived yet — they appear here automatically after the next scratchpad publish._")
        lines.append("")
        return "\n".join(lines)
    current_day = None
    for rec in sorted(records, key=lambda r: r.get("ts", ""), reverse=True):
        day = rec.get("date", "")
        if day != current_day:
            lines.append(f"\n## {day}\n")
            current_day = day
        t = rec.get("time", "")
        title_txt = rec.get("title") or rec.get("slug") or rec.get("rel", "")
        rel = rec.get("rel", "")
        url = rec.get("url", "")
        live = f" · [live]({url})" if url else ""
        lines.append(f"- **{t}** — [{title_txt}]({rel}) · slug `{rec.get('slug', '')}`{live}")
    lines.append("")
    return "\n".join(lines)


_HTML_INDEX_CSS = (
    ":root{color-scheme:light}*{box-sizing:border-box}"
    "body{margin:0;background:#fff;color:#1f2328;font:16px/1.6 -apple-system,BlinkMacSystemFont,"
    "'Segoe UI',Helvetica,Arial,sans-serif}"
    "main{max-width:920px;margin:0 auto;padding:2rem 1.2rem 5rem}"
    "h1{font-size:1.6rem;margin:0 0 .2rem}.sub{color:#656d76;font-size:.9rem;margin-bottom:1.2rem}"
    "input{width:100%;padding:.6rem .8rem;font-size:1rem;border:1px solid #d0d7de;border-radius:8px;margin-bottom:1.4rem}"
    "h2{font-size:1.05rem;color:#656d76;border-bottom:1px solid #d0d7de;padding-bottom:.3rem;margin:1.8rem 0 .6rem}"
    ".row{display:flex;gap:.6rem;align-items:baseline;padding:.32rem 0;border-bottom:1px solid #f0f1f3}"
    ".tm{color:#656d76;font:.8rem ui-monospace,Menlo,monospace;white-space:nowrap}"
    ".ti{flex:1}.ti a{color:#0969da;text-decoration:none;font-weight:600}.ti a:hover{text-decoration:underline}"
    ".sl{color:#8b949e;font:.74rem ui-monospace,Menlo,monospace}"
    ".live{font-size:.78rem;color:#1a7f37;text-decoration:none}"
)

_HTML_INDEX_JS = (
    "var q=document.getElementById('q');q.addEventListener('input',function(){"
    "var v=q.value.toLowerCase();document.querySelectorAll('.row').forEach(function(r){"
    "r.style.display=r.dataset.k.indexOf(v)>=0?'':'none';});"
    "document.querySelectorAll('h2').forEach(function(h){var n=h.nextElementSibling,s=false;"
    "while(n&&n.classList&&n.classList.contains('row')){if(n.style.display!=='none')s=true;n=n.nextElementSibling;}"
    "h.style.display=s?'':'none';});});"
)


def _render_html_index(root: Path, records: list[dict]) -> str:
    rows = []
    if not records:
        rows.append(
            '<div class="row" data-k=""><span class="ti">No artifacts archived yet — '
            "they appear here automatically after the next scratchpad publish.</span></div>"
        )
    current_day = None
    for rec in sorted(records, key=lambda r: r.get("ts", ""), reverse=True):
        day = rec.get("date", "")
        if day != current_day:
            rows.append(f"<h2>{_html.escape(day)}</h2>")
            current_day = day
        title_txt = rec.get("title") or rec.get("slug") or rec.get("rel", "")
        rel = rec.get("rel", "")
        url = rec.get("url", "")
        slug = rec.get("slug", "")
        key = f"{title_txt} {slug} {day}".lower()
        live = f'<a class="live" href="{_html.escape(url)}">live ↗</a>' if url else ""
        rows.append(
            f'<div class="row" data-k="{_html.escape(key)}">'
            f'<span class="tm">{_html.escape(rec.get("time", ""))}</span>'
            f'<span class="ti"><a href="{_html.escape(rel)}">{_html.escape(title_txt)}</a> '
            f'<span class="sl">{_html.escape(slug)}</span></span>{live}</div>'
        )
    return (
        "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        f"<title>Artifact archive — {_html.escape(root.name)}</title>"
        f"<style>{_HTML_INDEX_CSS}</style></head><body><main>"
        f"<h1>Artifact archive — {_html.escape(root.name)}</h1>"
        f'<div class="sub">{len(records)} artifact(s) · durable, dated copies of every scratchpad exhibit · newest first</div>'
        '<input id="q" type="search" placeholder="Filter by title, slug, or date…" autocomplete="off">'
        + "".join(rows)
        + f"</main><script>{_HTML_INDEX_JS}</script></body></html>\n"
    )


# --------------------------------------------------------------------------------------
# Archive one artifact.
# --------------------------------------------------------------------------------------
def archive_artifact(
    *,
    src: Path,
    slug: str,
    root: Path,
    is_dir: bool,
    url: str | None,
    now: datetime | None = None,
) -> dict:
    """Copy ``src`` into ``root`` under a dated/named path and update the indexes.

    Returns the ledger record. Raises on a real copy failure (the caller suppresses it).
    """
    now = now or datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M:%S")
    stamp = now.strftime("%H%M%S")
    iso = now.isoformat(timespec="seconds")

    day_dir = root / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    if is_dir:
        dest_dir = day_dir / f"{stamp}__{slug}"
        # Collision guard: a second publish in the same second gets a numeric suffix.
        n = 1
        while dest_dir.exists():
            dest_dir = day_dir / f"{stamp}__{slug}__{n}"
            n += 1
        shutil.copytree(src, dest_dir)
        title, entry = _docset_title_and_entry(src, slug)
        rel = f"{date_str}/{dest_dir.name}/{entry}" if entry else f"{date_str}/{dest_dir.name}/"
        kind = "docset"
    else:
        safe_name = Path(src).name or "report.html"
        dest = day_dir / f"{stamp}__{slug}__{safe_name}"
        n = 1
        while dest.exists():
            dest = day_dir / f"{stamp}__{slug}__{n}__{safe_name}"
            n += 1
        shutil.copy2(src, dest)
        title = _derive_title(Path(src), slug)
        rel = f"{date_str}/{dest.name}"
        kind = "file"

    record = {
        "ts": iso,
        "date": date_str,
        "time": time_str,
        "slug": slug,
        "title": title,
        "kind": kind,
        "rel": rel,
        "url": url or "",
    }

    with _Lock(root / LOCK_NAME):
        with open(root / LEDGER_NAME, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        records = _read_ledger(root)
        (root / MD_INDEX_NAME).write_text(_render_markdown_index(root, records), encoding="utf-8")
        (root / HTML_INDEX_NAME).write_text(_render_html_index(root, records), encoding="utf-8")

    return record


def ensure_index(root: Path) -> int:
    """(Re)build ``INDEX.md`` + ``index.html`` from the current ledger, empty-state if none.

    Idempotent. Used by ``publish_scratch.sh --init`` so the served ``/scratch-archive`` page
    always renders a proper page (e.g. "no artifacts yet") even before the first artifact
    lands — a directory with no ``index.html`` serves blank. Returns the artifact count.
    """
    root.mkdir(parents=True, exist_ok=True)
    records = _read_ledger(root)
    with _Lock(root / LOCK_NAME):
        (root / MD_INDEX_NAME).write_text(_render_markdown_index(root, records), encoding="utf-8")
        (root / HTML_INDEX_NAME).write_text(_render_html_index(root, records), encoding="utf-8")
    return len(records)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Archive a scratchpad artifact (durable, dated, indexed).")
    ap.add_argument("--src", help="the file or directory that was published")
    ap.add_argument("--slug", help="the publish slug")
    ap.add_argument("--root", required=True, help="archive root dir (per-project or VPS permanent)")
    ap.add_argument("--dir", action="store_true", dest="is_dir", help="src is a directory (docset)")
    ap.add_argument("--url", default="", help="the live tailnet URL of the published artifact")
    ap.add_argument(
        "--ensure-index",
        action="store_true",
        dest="ensure_index",
        help="(re)build the index files only (empty-state if no artifacts); used by --init",
    )
    args = ap.parse_args(argv)

    if (os.getenv("UA_SCRATCH_ARCHIVE_ENABLED") or "1").strip() == "0":
        return 0

    root = Path(args.root)

    if args.ensure_index:
        n = ensure_index(root)
        print(f"index ensured ({n} artifact(s))")
        return 0

    if not args.src or not args.slug:
        ap.error("--src and --slug are required unless --ensure-index is given")

    src = Path(args.src)
    if not src.exists():
        logger.warning("scratch archive skipped: %s does not exist", src)
        return 1
    root.mkdir(parents=True, exist_ok=True)

    rec = archive_artifact(
        src=src,
        slug=args.slug,
        root=root,
        is_dir=args.is_dir or src.is_dir(),
        url=args.url or None,
    )
    print(rec["rel"])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — best-effort; never break the caller's publish
        logging.getLogger(__name__).warning("scratch archive failed", exc_info=True)
        sys.exit(1)
