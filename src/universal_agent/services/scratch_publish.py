"""Publish operator-facing artifacts to the tailnet HTML scratchpad from Python.

Background: the operator (Kevin) runs Claude Code terminal-only and reads mail in
clients that strip hyperlinks and flatten HTML/PDF attachments. That kills the two
things that make a report useful — real rendering (styling, diagrams) and an in-page
table of contents whose entries actually *jump* to a section. The tailnet HTML
scratchpad fixes both: it serves files at a URL reachable ONLY from the operator's own
tailnet devices, so a published report renders perfectly and all anchors are live. It
doubles as the easiest way for an agent to hand the operator visual info or a file
without making him open an IDE.

``scripts/publish_scratch.sh`` is the single source of truth for the mechanism (it
auto-detects VPS-vs-remote and prints the URL). An LLM-driven agent reaches for it via
the ``publish-to-scratchpad`` skill, but a cron script or service has no LLM in the loop
and can't invoke a skill — so this helper wraps the SAME script, giving Python callers
one mechanism with no logic duplication or drift.

Four front doors, one mechanism:
    * ``publish_html_to_scratch``     — a rendered HTML string (the original).
    * ``publish_markdown_to_scratch`` — a markdown string → styled, light-mode HTML.
    * ``publish_file_to_scratch``     — any single file as-is (image, PDF, CSV, …).
    * ``publish_docset_to_scratch``   — a folder of markdown + files → a cross-linked
                                        HTML site (nav bar, subdirs preserved) under
                                        ONE slug, so inter-doc links resolve.

This is for OPERATOR-FACING artifacts only. The scratchpad is tailnet-only; the link is
dead off-tailnet, so never use it for mail to external recipients.
"""

from __future__ import annotations

from datetime import datetime, timezone
import html as _html
import json
import logging
from pathlib import Path
import posixpath
import re
import secrets
import shutil
import subprocess
import tempfile

from universal_agent.artifacts import repo_root

logger = logging.getLogger(__name__)

_SLUG_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Per-artifact metadata sidecar dropped into a slug dir; read by the index builder
# (``scripts/build_scratch_index.py``) to render the browsable artifact index.
_SIDECAR_NAME = "_artifact.json"

# Markdown extensions: tables/fenced-code/etc. (``extra``), header anchors for a
# working in-page TOC (``toc``), and forgiving list parsing (``sane_lists``).
_MD_RENDER_EXTENSIONS = ["extra", "toc", "sane_lists"]

# How docset files are handled by extension.
_MD_EXTS = {".md", ".markdown", ".mdown", ".mkd"}
# Text-ish code/data → rendered as an escaped "source view" page (+ a raw copy alongside).
_SOURCE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".yaml", ".yml", ".json",
    ".jsonl", ".toml", ".ini", ".cfg", ".txt", ".csv", ".tsv", ".sql", ".css",
    ".env", ".rb", ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp",
}
# Served raw, by MIME (the browser displays/downloads them directly).
_RAW_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".avif",
    ".pdf", ".html", ".htm",
}

# The scratchpad is always served under this path (``tailscale serve --set-path
# /scratch``) and the browsable artifact index lives at its root. An absolute href
# lets a "back to index" link resolve from any artifact depth — ``/scratch/<slug>/
# <file>`` or a nested docset page alike.
SCRATCH_INDEX_HREF = "/scratch/"


def scratch_back_link_html(label: str = "Scratchpad index") -> str:
    """A small 'back to the artifact index' link for any scratchpad-served page.

    Renders ``<div class="scratch-back">…</div>``; each page styles ``.scratch-back``
    in its own CSS. Centralised so every producer — the markdown/docset pages here and
    the YouTube digest renderer — agrees on the index target and wording.
    """
    return (
        f'<div class="scratch-back"><a href="{SCRATCH_INDEX_HREF}">'
        f"← {_html.escape(label)}</a></div>"
    )


def _publish_script() -> Path:
    """Absolute path to the canonical publish script for this checkout."""
    return repo_root() / "scripts" / "publish_scratch.sh"


def _sanitize_slug(slug: str) -> str:
    """Coerce an arbitrary string into the ``[A-Za-z0-9._-]+`` slug the script accepts."""
    cleaned = _SLUG_SANITIZE_RE.sub("-", slug).strip("-._")
    return cleaned or "report"


def _build_slug(slug: str | None) -> str:
    """A readable prefix (if given) plus a random suffix for collision-free, unguessable URLs."""
    suffix = secrets.token_hex(3)
    return f"{_sanitize_slug(slug)}-{suffix}" if slug else suffix


def _resolve_slug(slug: str | None, artifact_id: str | None) -> str:
    """Pick the URL subdir name.

    ``artifact_id`` → a STABLE slug (exactly that id, no random suffix): re-publishing
    overwrites the same dir and serves at the same URL, so it's one living exhibit you
    keep refining instead of a pile of one-off artifacts. Any comment sidecars already in
    that dir survive (they aren't in the freshly rendered tree). ``artifact_id=None`` keeps
    the random-suffixed one-off slug.
    """
    if artifact_id:
        return _sanitize_slug(artifact_id)
    return _build_slug(slug)


def _run_publish_script(args: list[str], timeout: float) -> str | None:
    """Run ``publish_scratch.sh`` with ``args``; return the printed ``https://`` URL or ``None``.

    Centralises the best-effort contract every front door shares: a missing script,
    non-zero exit, timeout, or non-URL output all degrade to ``None`` (never raise), so
    callers can fall back to attaching/ pasting the artifact — a raw-but-delivered report
    always beats a dropped one.
    """
    script = _publish_script()
    if not script.exists():
        logger.warning("scratch publish skipped: %s not found", script)
        return None
    try:
        proc = subprocess.run(
            [str(script), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("scratch publish timed out after %ss", timeout)
        return None
    except Exception:  # noqa: BLE001 — publishing is best-effort; never break the caller
        logger.warning("scratch publish raised", exc_info=True)
        return None

    if proc.returncode != 0:
        logger.warning(
            "scratch publish failed (rc=%s): %s",
            proc.returncode,
            (proc.stderr or "").strip()[-500:],
        )
        return None

    url = (proc.stdout or "").strip()
    if not url.startswith("https://"):
        logger.warning("scratch publish produced unexpected output: %r", url[:200])
        return None
    return url


def _write_sidecar(dest_dir: Path, *, title: str, description: str, kind: str, entry: str) -> None:
    """Write the ``_artifact.json`` metadata sidecar into a slug dir for the index builder."""
    record = {
        "schema": 1,
        "title": title or entry,
        "description": description or "",
        "kind": kind,
        "entry": entry,  # the file the index should link to within the slug dir
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (dest_dir / _SIDECAR_NAME).write_text(json.dumps(record, indent=2), encoding="utf-8")


def _publish_one(
    *,
    filename: str,
    slug: str | None,
    title: str | None,
    description: str | None,
    kind: str,
    timeout: float,
    content: str | None = None,
    src_path: Path | None = None,
    artifact_id: str | None = None,
) -> str | None:
    """Publish a single artifact (string content or a source file).

    With ``title``/``description`` it bundles an ``_artifact.json`` sidecar and ships the
    pair via the directory path (so the artifact lands in the index with rich metadata).
    Without metadata it uses the plain single-file path — an unchanged, battle-tested
    contract for existing callers like the YouTube digest. Either way the artifact URL is
    ``/scratch/<slug>/<filename>``.
    """
    safe_name = Path(filename).name or "report.html"

    if title is None and description is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="ua_scratch_"))
        tmp_file = tmp_dir / safe_name
        try:
            if content is not None:
                tmp_file.write_text(content, encoding="utf-8")
            else:
                shutil.copy2(src_path, tmp_file)  # type: ignore[arg-type]
            return _run_publish_script([str(tmp_file), _resolve_slug(slug, artifact_id)], timeout)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    tmp_dir = Path(tempfile.mkdtemp(prefix="ua_artifact_"))
    try:
        dst = tmp_dir / safe_name
        if content is not None:
            dst.write_text(content, encoding="utf-8")
        else:
            shutil.copy2(src_path, dst)  # type: ignore[arg-type]
        _write_sidecar(tmp_dir, title=title or safe_name, description=description or "", kind=kind, entry=safe_name)
        base = _publish_dir(tmp_dir, slug=slug, timeout=timeout, artifact_id=artifact_id)
        return (base + safe_name) if base else None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def publish_html_to_scratch(
    html: str,
    *,
    slug: str | None = None,
    filename: str = "report.html",
    title: str | None = None,
    description: str | None = None,
    artifact_id: str | None = None,
    timeout: float = 90.0,
) -> str | None:
    """Publish an HTML string to the tailnet scratchpad; return its URL or ``None``.

    Args:
        html: The rendered HTML document, as a string.
        slug: Optional human-readable prefix for the URL subdir. A short random suffix
            is always appended so repeated runs never collide and the URL stays
            unguessable. When ``None``, a fully random slug is used.
        filename: The file name within the slug dir; becomes the last URL segment.
        title: Optional artifact title for the browsable index. When ``title`` or
            ``description`` is given, a metadata sidecar is published alongside.
        description: Optional one-line description for the index.
        artifact_id: Optional STABLE id. When set, re-publishing overwrites the same URL
            (one living exhibit you keep refining) instead of minting a new random slug.
        timeout: Seconds to wait for the publish (covers the ``ssh``/``scp`` path when
            run off the VPS).

    Returns:
        The published ``https://`` URL on success, or ``None`` on any failure.
    """
    return _publish_one(
        content=html,
        filename=filename,
        slug=slug,
        title=title,
        description=description,
        kind="html",
        timeout=timeout,
        artifact_id=artifact_id,
    )


def publish_file_to_scratch(
    path: str | Path,
    *,
    slug: str | None = None,
    title: str | None = None,
    description: str | None = None,
    artifact_id: str | None = None,
    timeout: float = 90.0,
) -> str | None:
    """Publish any single file as-is (image, PDF, CSV, …); return its URL or ``None``.

    The file is served by its extension's MIME type, so the operator can view a diagram,
    screenshot, or data file in the browser without opening an IDE. Pass ``title`` /
    ``description`` to surface it richly in the artifact index, or ``artifact_id`` for a
    stable URL that re-publishing overwrites. For rendered markdown use
    :func:`publish_markdown_to_scratch`; for a whole folder use
    :func:`publish_docset_to_scratch`.
    """
    p = Path(path)
    if not p.is_file():
        logger.warning("scratch file publish skipped: %s is not a file", p)
        return None
    return _publish_one(
        src_path=p,
        filename=p.name,
        slug=slug,
        title=title,
        description=description,
        kind="file",
        timeout=timeout,
        artifact_id=artifact_id,
    )


def publish_markdown_to_scratch(
    markdown_text: str,
    *,
    slug: str | None = None,
    title: str | None = None,
    description: str | None = None,
    artifact_id: str | None = None,
    filename: str = "report.html",
    timeout: float = 90.0,
) -> str | None:
    """Render a markdown string to a styled, light-mode HTML page and publish it.

    Returns the published URL or ``None``. Use this for a single document; for a
    cross-linked set of docs use :func:`publish_docset_to_scratch`. The title defaults to
    the doc's first ``# H1`` and is recorded in the artifact index. Pass ``artifact_id``
    for a stable URL that re-publishing refines in place.
    """
    page_title = title or _markdown_title(markdown_text, "Report")
    body = _render_markdown(markdown_text)
    html = _html_page(page_title, body)
    return _publish_one(
        content=html,
        filename=filename,
        slug=slug,
        title=page_title,
        description=description,
        kind="markdown",
        timeout=timeout,
        artifact_id=artifact_id,
    )


def publish_docset_to_scratch(
    src_dir: str | Path,
    *,
    slug: str | None = None,
    hub: str | None = None,
    title: str | None = None,
    description: str | None = None,
    artifact_id: str | None = None,
    timeout: float = 180.0,
) -> str | None:
    """Render a folder of markdown + files into a cross-linked HTML site and publish it.

    Every ``.md`` becomes a styled, light-mode page; code/data files become escaped
    "source view" pages (with a raw copy alongside); images/PDFs/HTML are served as-is.
    A nav bar on every page makes the set browsable, subdirectories are preserved, and
    ``[text](other.md)`` links are rewritten to the rendered ``.html`` — all published
    under ONE slug so the links resolve.

    Args:
        src_dir: The folder to publish.
        slug: Optional readable prefix (a random suffix is always appended).
        hub: The landing page, given as a source relpath (``"DESIGN.md"``) or output
            relpath (``"DESIGN.html"``). Defaults to DESIGN/README/index, else the first
            markdown file. The returned URL points at the hub.
        title: Optional title for the artifact index (defaults to the hub page's title).
        description: Optional one-line description for the index.
        artifact_id: Optional STABLE id — re-publishing overwrites the same URL (refine the
            same doc set in place) instead of minting a new random slug.
        timeout: Seconds to wait (the tree publish copies more than a single file).

    Returns:
        The hub's ``https://`` URL on success, or ``None`` on any failure.
    """
    src = Path(src_dir)
    if not src.is_dir():
        logger.warning("docset publish skipped: %s is not a directory", src)
        return None

    specs = _discover_docset(src)
    if not specs:
        logger.warning("docset publish skipped: no renderable files under %s", src)
        return None

    hub_out = _resolve_hub(specs, hub) or _pick_hub(specs)

    out_dir = Path(tempfile.mkdtemp(prefix="ua_docset_"))
    try:
        _render_docset_tree(specs, out_dir, hub_out=hub_out, brand=src.name)
        hub_spec = next((s for s in specs if s["out"] == hub_out), None)
        _write_sidecar(
            out_dir,
            title=title or (hub_spec["title"] if hub_spec else src.name),
            description=description or "",
            kind="docset",
            entry=hub_out or "",
        )
        base = _publish_dir(out_dir, slug=slug, timeout=timeout, artifact_id=artifact_id)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    if not base:
        return None
    return base + hub_out


def _publish_dir(out_dir: Path, *, slug: str | None, timeout: float, artifact_id: str | None = None) -> str | None:
    """Publish a whole local directory tree under one slug; return the base URL (ends with ``/``)."""
    url = _run_publish_script(["--dir", str(out_dir), _resolve_slug(slug, artifact_id)], timeout)
    if url and not url.endswith("/"):
        url += "/"
    return url


# --------------------------------------------------------------------------------------
# Rendering (pure; no network) — markdown → styled HTML, source views, nav, link rewrite.
#
# Light mode is MANDATORY for scratchpad pages: Kevin reads them on a phone that is often
# in dark mode, and a page that auto-inverts (or half-inverts) is hard to read and looks
# broken. So we pin ``color-scheme: light`` and set explicit light bg/dark text, and we
# never emit a ``prefers-color-scheme: dark`` block. Mirrors the publish-to-scratchpad
# skill's "light mode is mandatory" rule.
# --------------------------------------------------------------------------------------

_DOCSET_CSS = """
:root{color-scheme:light;--fg:#1f2328;--muted:#656d76;--bg:#ffffff;--soft:#f6f8fa;--border:#d0d7de;--accent:#0969da;--accent-soft:#ddf4ff}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:#ffffff;color:#1f2328;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;line-height:1.6;font-size:16px}
nav.docset{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.92);backdrop-filter:blur(6px);
  border-bottom:1px solid var(--border);padding:.55rem 1rem;display:flex;flex-wrap:wrap;gap:.4rem;align-items:center}
nav.docset .brand{font-weight:700;margin-right:.6rem;font-size:.9rem;color:var(--muted)}
nav.docset a{font-size:.82rem;text-decoration:none;color:var(--accent);padding:.2rem .55rem;border-radius:6px;white-space:nowrap}
nav.docset a:hover{background:var(--accent-soft)}
nav.docset a.current{background:var(--fg);color:#fff;font-weight:600}
nav.docset .sep{width:1px;height:1.1rem;background:var(--border);margin:0 .2rem}
nav.docset a.file{color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.76rem}
.scratch-back{padding:.5rem 1rem;font-size:.82rem;background:var(--soft);border-bottom:1px solid var(--border)}
.scratch-back a{color:var(--accent);text-decoration:none}
.scratch-back a:hover{text-decoration:underline}
main{max-width:880px;margin:0 auto;padding:2.2rem 1.3rem 5rem}
h1,h2,h3,h4{line-height:1.25;margin-top:1.8em;margin-bottom:.6em;font-weight:650}
h1{font-size:1.9rem;border-bottom:1px solid var(--border);padding-bottom:.3em;margin-top:.2em}
h2{font-size:1.45rem;border-bottom:1px solid var(--border);padding-bottom:.25em}
h3{font-size:1.18rem}
a{color:var(--accent)}
p,li{overflow-wrap:break-word}
code{background:var(--soft);padding:.15em .4em;border-radius:6px;font-size:.86em;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:var(--soft);border:1px solid var(--border);border-radius:8px;padding:1rem;overflow-x:auto;line-height:1.45}
pre code{background:none;padding:0;font-size:.82rem}
blockquote{margin:1em 0;padding:.2em 1em;color:var(--muted);border-left:4px solid var(--border)}
table{border-collapse:collapse;width:100%;margin:1.2em 0;font-size:.92rem;display:block;overflow-x:auto}
th,td{border:1px solid var(--border);padding:.5em .7em;text-align:left;vertical-align:top}
th{background:var(--soft);font-weight:650}
tr:nth-child(even) td{background:#fbfcfd}
hr{border:none;border-top:1px solid var(--border);margin:2em 0}
.srcmeta{color:var(--muted);font-size:.85rem;margin:.4rem 0 1rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
footer{max-width:880px;margin:0 auto;padding:1.5rem 1.3rem;color:var(--muted);font-size:.78rem;border-top:1px solid var(--border)}
"""


# --------------------------------------------------------------------------------------
# Two-way review toolbar (Phase 1: no backend). Baked into every rendered page so the
# operator can mark it up, then click one button that (a) downloads the comments JSON to
# ~/Downloads and (b) copies a ready-to-paste ``[scratch-review <slug>]`` prompt to the
# clipboard. Pasting that prompt into Claude Code is the whole continuation — no server,
# no project task hub. The toolbar derives the artifact slug from the URL at runtime, so
# it needs nothing from publish time. Plain (non-f) strings: the braces are literal.
# --------------------------------------------------------------------------------------

_REVIEW_TOOLBAR_CSS = """
.sr-fab{position:fixed;right:16px;bottom:16px;z-index:2147483000;background:#0969da;color:#fff;border:none;border-radius:999px;padding:.55rem .9rem;font:600 13px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.25);cursor:pointer}
.sr-fab:hover{background:#0a5fd0}
.sr-panel{position:fixed;right:16px;bottom:64px;z-index:2147483000;width:340px;max-width:92vw;max-height:70vh;background:#fff;color:#1f2328;border:1px solid #d0d7de;border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.18);display:none;flex-direction:column;overflow:hidden;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.sr-panel.sr-open{display:flex}
.sr-head{display:flex;justify-content:space-between;align-items:center;padding:.6rem .8rem;border-bottom:1px solid #eaecef}
.sr-x,.sr-del{background:none;border:none;color:#656d76;cursor:pointer;font-size:13px}
.sr-list{overflow:auto;padding:.5rem .8rem;flex:1}
.sr-empty{color:#656d76;font-size:.85rem;padding:.5rem 0}
.sr-item{border:1px solid #eaecef;border-radius:8px;padding:.45rem .55rem;margin-bottom:.5rem}
.sr-loc{display:flex;justify-content:space-between;font-size:.72rem;color:#656d76;font-family:ui-monospace,Menlo,monospace;margin-bottom:.25rem}
.sr-quote{font-size:.8rem;color:#3b4148;background:#f6f8fa;border-left:3px solid #d0d7de;padding:.2rem .5rem;margin-bottom:.3rem}
.sr-body{width:100%;border:1px solid #d0d7de;border-radius:6px;padding:.35rem .45rem;font:inherit;resize:vertical}
.sr-actions{display:flex;gap:.4rem;padding:.6rem .8rem;border-top:1px solid #eaecef}
.sr-note{background:#f6f8fa;border:1px solid #d0d7de;border-radius:6px;padding:.4rem .6rem;cursor:pointer;font:inherit}
.sr-submit{flex:1;background:#1f883d;color:#fff;border:none;border-radius:6px;padding:.4rem .6rem;cursor:pointer;font:600 13px/1 inherit}
.sr-submit:hover{background:#1a7332}
.sr-chip{position:absolute;z-index:2147483001;background:#1f2328;color:#fff;border:none;border-radius:6px;padding:.25rem .5rem;font:600 12px/1 sans-serif;cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.3)}
.sr-toast{position:fixed;left:50%;bottom:84px;transform:translateX(-50%);z-index:2147483002;background:#1f2328;color:#fff;padding:.55rem .9rem;border-radius:8px;font:13px/1.4 sans-serif;max-width:90vw;text-align:center;box-shadow:0 4px 16px rgba(0,0,0,.3)}
@media print{.sr-fab,.sr-panel,.sr-chip,.sr-toast{display:none!important}}
"""

_REVIEW_TOOLBAR_JS = r"""
(function(){
  "use strict";
  var m = location.pathname.match(/\/scratch\/([^\/]+)\//);
  var parts = location.pathname.split('/').filter(Boolean);
  var slug = m ? m[1] : (parts.length>1 ? parts[parts.length-2] : 'artifact');
  var KEY = 'scratchReview:' + slug;
  var comments = [];
  try { comments = JSON.parse(localStorage.getItem(KEY) || '[]') || []; } catch(e){}
  function persist(){ try{ localStorage.setItem(KEY, JSON.stringify(comments)); }catch(e){} }
  function save(){ persist(); render(); }
  function esc(s){ return (s||'').replace(/[&<>"]/g,function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[ch];}); }

  function headingFor(node){
    var hs = document.querySelectorAll('main h1[id],main h2[id],main h3[id],main h4[id]');
    var best=null;
    for(var i=0;i<hs.length;i++){
      if(hs[i]===node || (node.compareDocumentPosition(hs[i]) & Node.DOCUMENT_POSITION_PRECEDING)) best=hs[i];
    }
    return best;
  }

  var btn=document.createElement('button'); btn.className='sr-fab'; btn.type='button';
  var panel=document.createElement('div'); panel.className='sr-panel';
  panel.innerHTML='<div class="sr-head"><b>Review</b><button type="button" class="sr-x" data-act="close">✕</button></div>'
    +'<div class="sr-list"></div>'
    +'<div class="sr-actions"><button type="button" class="sr-note" data-act="note">+ Note</button>'
    +'<button type="button" class="sr-submit" data-act="submit">Submit → copy prompt</button></div>';
  var chip=document.createElement('button'); chip.type='button'; chip.className='sr-chip'; chip.textContent='💬 Comment'; chip.style.display='none';
  var toast=document.createElement('div'); toast.className='sr-toast'; toast.style.display='none';
  document.body.appendChild(btn); document.body.appendChild(panel); document.body.appendChild(chip); document.body.appendChild(toast);

  function showToast(t){ toast.textContent=t; toast.style.display='block'; setTimeout(function(){toast.style.display='none';},4500); }
  function openPanel(o){ panel.classList.toggle('sr-open', o!==false); }

  function render(){
    btn.textContent='💬 Review'+(comments.length?' ('+comments.length+')':'');
    var list=panel.querySelector('.sr-list');
    if(!comments.length){ list.innerHTML='<div class="sr-empty">Select text on the page to comment, or add a note.</div>'; return; }
    list.innerHTML='';
    comments.forEach(function(c,i){
      var row=document.createElement('div'); row.className='sr-item';
      row.innerHTML='<div class="sr-loc"><span>'+esc(c.heading?'§ '+c.heading:'note')+'</span><button type="button" class="sr-del" data-i="'+i+'">✕</button></div>'
        +(c.quote?'<div class="sr-quote">'+esc(c.quote)+'</div>':'')
        +'<textarea class="sr-body" data-i="'+i+'" rows="2" placeholder="Your comment…">'+esc(c.body||'')+'</textarea>';
      list.appendChild(row);
    });
  }

  function addComment(quote, heading){
    comments.push({id:Date.now()+'-'+comments.length, quote:quote||'', heading:heading||'', body:''});
    save(); openPanel(true);
    var tas=panel.querySelectorAll('.sr-body'); if(tas.length) tas[tas.length-1].focus();
  }

  document.addEventListener('mouseup', function(){
    setTimeout(function(){
      var sel=window.getSelection(); var txt=sel && sel.toString().trim(); var main=document.querySelector('main');
      if(txt && txt.length>1 && sel.rangeCount && main && main.contains(sel.anchorNode)){
        var r=sel.getRangeAt(0).getBoundingClientRect();
        chip.style.top=(window.scrollY+r.bottom+6)+'px'; chip.style.left=(window.scrollX+r.left)+'px';
        chip.dataset.quote=txt.slice(0,280);
        var h=headingFor(sel.anchorNode); chip.dataset.heading=h?(h.textContent||'').replace(/¶|#/g,'').trim().slice(0,120):'';
        chip.style.display='block';
      } else { chip.style.display='none'; }
    },10);
  });
  chip.addEventListener('mousedown', function(e){ e.preventDefault(); addComment(chip.dataset.quote, chip.dataset.heading); chip.style.display='none'; var s=window.getSelection(); if(s) s.removeAllRanges(); });
  btn.addEventListener('click', function(){ openPanel(!panel.classList.contains('sr-open')); render(); });
  panel.addEventListener('click', function(e){
    var act=e.target.getAttribute('data-act');
    if(act==='close') openPanel(false);
    else if(act==='note') addComment('','');
    else if(act==='submit') submit();
    else if(e.target.classList.contains('sr-del')){ comments.splice(+e.target.getAttribute('data-i'),1); save(); }
  });
  panel.addEventListener('input', function(e){
    if(e.target.classList.contains('sr-body')){ comments[+e.target.getAttribute('data-i')].body=e.target.value; persist(); btn.textContent='💬 Review ('+comments.length+')'; }
  });

  function buildPrompt(){
    var title=document.title||slug; var L=[];
    L.push('[scratch-review '+slug+']');
    L.push('Review of "'+title+'"'); L.push(location.href);
    L.push('I left '+comments.length+' comment(s); full JSON downloaded to ~/Downloads/scratch-comments-'+slug+'.json'); L.push('');
    comments.forEach(function(c,i){
      L.push((i+1)+'. ['+(c.heading?'§ '+c.heading:'note')+'] '+(c.body||'(no text)'));
      if(c.quote) L.push('   ↳ re: "'+c.quote+'"');
    });
    L.push(''); L.push('Re-read the artifact and address each comment, then reply in our chat.');
    return L.join('\n');
  }
  function downloadJSON(){
    var payload={slug:slug,title:document.title,url:location.href,submitted_at:new Date().toISOString(),comments:comments};
    var blob=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});
    var a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='scratch-comments-'+slug+'.json';
    document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href); a.remove();},120);
  }
  function copyText(text){
    if(navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
    return new Promise(function(res,rej){ try{ var ta=document.createElement('textarea'); ta.value=text; ta.style.position='fixed'; ta.style.opacity='0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); res(); }catch(err){ rej(err); } });
  }
  function submit(){
    if(!comments.length){ showToast('No comments yet — select text or add a note first.'); return; }
    var text=buildPrompt(); downloadJSON();
    copyText(text).then(function(){ showToast('Copied — paste into Claude Code. JSON saved to ~/Downloads.'); })
                  .catch(function(){ showToast('JSON downloaded to ~/Downloads. Clipboard blocked — copy from the file.'); });
  }

  render();
})();
"""


def _render_markdown(markdown_text: str) -> str:
    """Markdown → HTML fragment. Lazy import keeps the HTML/file paths import-light."""
    import markdown  # noqa: PLC0415 — intentional lazy import

    md = markdown.Markdown(extensions=_MD_RENDER_EXTENSIONS)
    return md.convert(markdown_text)


def _html_page(title: str, body_html: str, *, nav_html: str = "", review: bool = True) -> str:
    """Wrap a body fragment in a complete, light-mode-pinned HTML document.

    ``review=True`` (default) bakes in the two-way review toolbar so the operator can mark
    the page up and copy a paste-ready ``[scratch-review <slug>]`` prompt. CSS/JS are
    concatenated (not f-string-interpolated) so their literal braces are safe.
    """
    review_css = _REVIEW_TOOLBAR_CSS if review else ""
    review_js = ("<script>" + _REVIEW_TOOLBAR_JS + "</script>\n") if review else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="color-scheme" content="light">\n'
        f"<title>{_html.escape(title)}</title>\n"
        "<style>" + _DOCSET_CSS + review_css + "</style>\n"
        "</head>\n"
        f"<body>{scratch_back_link_html()}{nav_html}<main>{body_html}</main>\n"
        "<footer>Rendered on the tailnet HTML scratchpad · private to your devices.</footer>\n"
        + review_js
        + "</body></html>\n"
    )


def _markdown_title(markdown_text: str, fallback: str) -> str:
    """First ``# H1`` of a markdown doc (stripped of inline markup), else ``fallback``."""
    m = re.search(r"^#\s+(.+?)\s*$", markdown_text, re.MULTILINE)
    if not m:
        return fallback
    return re.sub(r"[*_`]", "", m.group(1)).strip() or fallback


def _classify(suffix: str) -> str | None:
    s = suffix.lower()
    if s in _MD_EXTS:
        return "md"
    if s in _SOURCE_EXTS:
        return "source"
    if s in _RAW_EXTS:
        return "raw"
    return None


def _discover_docset(src_dir: Path) -> list[dict]:
    """Walk ``src_dir`` into render specs, skipping dotfiles and ``__pycache__``.

    Each spec: ``{src, rel, out, kind}`` where ``out`` is the published relpath
    (``.md``→``.html``; source→``<name>.html``; raw served as-is).
    """
    specs: list[dict] = []
    for p in sorted(src_dir.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(src_dir).parts
        if any(seg.startswith(".") for seg in rel_parts) or "__pycache__" in rel_parts:
            continue
        rel = p.relative_to(src_dir).as_posix()
        kind = _classify(p.suffix)
        if kind is None:
            logger.debug("docset: skipping unsupported file %s", rel)
            continue
        if kind == "md":
            out = rel[: -len(p.suffix)] + ".html"
        elif kind == "source":
            out = rel + ".html"
        else:
            out = rel
        specs.append({"src": p, "rel": rel, "out": out, "kind": kind})
    return specs


def _resolve_hub(specs: list[dict], hub: str | None) -> str | None:
    """Map an explicit ``hub`` (source relpath or output relpath) to its output relpath."""
    if not hub:
        return None
    for s in specs:
        if s["rel"] == hub or s["out"] == hub:
            return s["out"]
    logger.warning("docset: requested hub %r not found; falling back to default", hub)
    return None


def _pick_hub(specs: list[dict]) -> str | None:
    """Default landing page: DESIGN/README/index (top-level first), else first markdown."""
    md = [s for s in specs if s["kind"] == "md"]
    for cand in ("design.md", "readme.md", "index.md"):
        for s in md:  # specs are path-sorted, so top-level files come before nested ones
            if posixpath.basename(s["rel"]).lower() == cand:
                return s["out"]
    if md:
        return md[0]["out"]
    return specs[0]["out"] if specs else None


def _rewrite_links(markdown_text: str, doc_rel: str, out_map: dict[str, str]) -> str:
    """Rewrite ``[text](other.md)`` links that target a doc in the set → its rendered output.

    Resolves relative to the linking doc's own directory and re-relativises the result, so
    nested docs link correctly (``prototype/x.md`` → ``../DESIGN.html``). External
    (``://``), anchor-only (``#``), and unknown targets are left untouched.
    """
    doc_dir = posixpath.dirname(doc_rel)

    def repl(m: re.Match) -> str:
        text, target = m.group(1), m.group(2).strip()
        if "://" in target or target.startswith(("#", "mailto:", "/")):
            return m.group(0)
        path, frag = target, ""
        if "#" in path:
            path, _, rest = path.partition("#")
            frag = "#" + rest
        resolved = posixpath.normpath(posixpath.join(doc_dir, path)) if doc_dir else posixpath.normpath(path)
        mapped = out_map.get(resolved) or out_map.get(path)
        if not mapped:
            return m.group(0)
        new_rel = posixpath.relpath(mapped, doc_dir) if doc_dir else mapped
        return f"[{text}]({new_rel}{frag})"

    return re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", repl, markdown_text)


def _build_nav(specs: list[dict], current_out: str, hub_out: str | None, brand: str) -> str:
    """Sticky top nav: markdown pages first, then a monospace group of source/raw files."""
    cur_dir = posixpath.dirname(current_out)

    def href(out: str) -> str:
        return posixpath.relpath(out, cur_dir) if cur_dir else out

    md_links, file_links = [], []
    for s in specs:
        cls = "current" if s["out"] == current_out else ""
        label = _html.escape(s["title"])
        if s["kind"] == "md":
            tag = " · hub" if s["out"] == hub_out else ""
            md_links.append(f'<a class="{cls}" href="{href(s["out"])}">{label}{tag}</a>')
        else:
            file_links.append(f'<a class="file {cls}" href="{href(s["out"])}">{label}</a>')

    sep = '<span class="sep"></span>' if file_links else ""
    return (
        '<nav class="docset">'
        f'<span class="brand">{_html.escape(brand)}</span>'
        + "".join(md_links) + sep + "".join(file_links)
        + "</nav>"
    )


def _render_docset_tree(specs: list[dict], out_dir: Path, *, hub_out: str | None, brand: str) -> str | None:
    """Render every spec into ``out_dir`` (subdirs preserved). Returns ``hub_out``.

    Pure: writes only under ``out_dir``, no network. Mutates each spec with a ``title``.
    """
    # Pre-pass: titles (needed by the nav on every page).
    for s in specs:
        if s["kind"] == "md":
            s["title"] = _markdown_title(
                s["src"].read_text(encoding="utf-8", errors="replace"),
                posixpath.basename(s["rel"]),
            )
        else:
            s["title"] = posixpath.basename(s["rel"])

    out_map = {s["rel"]: s["out"] for s in specs}

    for s in specs:
        nav = _build_nav(specs, s["out"], hub_out, brand)
        dst = out_dir / s["out"]
        dst.parent.mkdir(parents=True, exist_ok=True)

        if s["kind"] == "md":
            text = s["src"].read_text(encoding="utf-8", errors="replace")
            body = _render_markdown(_rewrite_links(text, s["rel"], out_map))
            dst.write_text(_html_page(s["title"], body, nav_html=nav), encoding="utf-8")
        elif s["kind"] == "source":
            raw = s["src"].read_text(encoding="utf-8", errors="replace")
            raw_href = posixpath.basename(s["rel"])
            body = (
                f"<h1>{_html.escape(s['title'])}</h1>"
                f'<p class="srcmeta">source view · {len(raw.splitlines())} lines · '
                f'<a href="{raw_href}">raw</a></p>'
                f"<pre><code>{_html.escape(raw)}</code></pre>"
            )
            dst.write_text(_html_page(s["title"], body, nav_html=nav), encoding="utf-8")
            # Also drop the raw file alongside, so the "raw" link (and any direct path) resolves.
            raw_dst = out_dir / s["rel"]
            raw_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s["src"], raw_dst)
        else:  # raw — served by MIME
            shutil.copy2(s["src"], dst)

    return hub_out
