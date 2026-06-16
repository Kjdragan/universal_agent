#!/usr/bin/env python3
"""Build the browsable index for the tailnet HTML scratchpad artifact store.

The scratchpad (`/home/ua/ua_scratch/<slug>/<files>`, served tailnet-only via
`tailscale serve --set-path /scratch`) is the operator's persistent artifact store. This
script scans every slug dir, reads each artifact's `_artifact.json` sidecar (falling back
to deriving title/date from the files when there is none), and writes a single
self-contained `index.html` at the scratch root — a date-sorted, client-side-searchable
list of title · date · description, each linking to its artifact.

It is **stdlib-only** so it runs anywhere with plain `python3` (no venv, no deps): on the
VPS after every publish (wired into `scripts/publish_scratch.sh`) and from a cron/timer.

Reachable at  https://uaonvps.taildcc090.ts.net/scratch/  (and /scratch/index.html).

Canonical reference: project_docs/06_platform/06_networking_tailscale_proxy_sshfs.md § 1.6
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re

DEFAULT_ROOT = os.environ.get("UA_SCRATCH_ROOT", "/home/ua/ua_scratch")
DEFAULT_TS_HOST = os.environ.get("UA_SCRATCH_TS_HOST", "uaonvps.taildcc090.ts.net")
SIDECAR_NAME = "_artifact.json"
INDEX_NAME = "index.html"

_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def _pick_entry(slug_dir: Path) -> str | None:
    """Choose the file an index row should link to when there's no sidecar."""
    files = [p for p in sorted(slug_dir.rglob("*")) if p.is_file() and p.name != SIDECAR_NAME]
    if not files:
        return None
    rels = [p.relative_to(slug_dir).as_posix() for p in files]
    for preferred in ("index.html", "DESIGN.html", "README.html"):
        for r in rels:
            if r == preferred:
                return r
    for r in rels:  # any top-level html
        if r.endswith(".html") and "/" not in r:
            return r
    for r in rels:  # any html
        if r.endswith(".html"):
            return r
    return rels[0]


def _derive_title(slug_dir: Path, entry: str) -> str:
    """Title from the entry file's <title>/<h1>, else the slug name."""
    target = slug_dir / entry
    try:
        head = target.read_text(encoding="utf-8", errors="replace")[:8192]
    except OSError:
        head = ""
    for rx in (_TITLE_RE, _H1_RE):
        m = rx.search(head)
        if m:
            # The match is HTML text (may contain entities); unescape to plain text so the
            # index renderer escapes it exactly once (avoids &amp;amp;).
            title = html.unescape(_strip_tags(m.group(1)))
            if title:
                return title
    return slug_dir.name


def _read_artifact(slug_dir: Path) -> dict | None:
    """One index record for a slug dir, from its sidecar or derived from its files."""
    entry: str | None = None
    title = description = ""
    kind = "artifact"
    created_at: str | None = None

    sidecar = slug_dir / SIDECAR_NAME
    if sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            entry = data.get("entry") or None
            title = (data.get("title") or "").strip()
            description = (data.get("description") or "").strip()
            kind = (data.get("kind") or "artifact").strip() or "artifact"
            created_at = data.get("created_at") or None
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    if not entry or not (slug_dir / entry).is_file():
        entry = _pick_entry(slug_dir)
    if entry is None:
        return None  # empty dir — nothing to link to
    if not title:
        title = _derive_title(slug_dir, entry)
    if not created_at:
        ts = (slug_dir / entry).stat().st_mtime
        created_at = datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")

    return {
        "slug": slug_dir.name,
        "entry": entry,
        "title": title,
        "description": description,
        "kind": kind,
        "created_at": created_at,
    }


def _sort_key(rec: dict) -> str:
    # ISO-8601 strings sort lexicographically by time; missing → empty (oldest).
    return rec.get("created_at") or ""


def collect_artifacts(root: Path) -> list[dict]:
    """All artifact records under ``root``, newest first."""
    records = []
    for child in sorted(root.iterdir()) if root.is_dir() else []:
        if not child.is_dir() or child.name.startswith("."):
            continue
        rec = _read_artifact(child)
        if rec:
            records.append(rec)
    records.sort(key=_sort_key, reverse=True)
    return records


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso[:16]


def render_index(records: list[dict], ts_host: str) -> str:
    """Self-contained, light-mode, client-side-searchable index page."""
    rows = []
    for r in records:
        url = f"https://{ts_host}/scratch/{r['slug']}/{r['entry']}"
        title = html.escape(r["title"] or r["slug"])
        desc = html.escape(r["description"])
        kind = html.escape(r["kind"])
        date_disp = html.escape(_fmt_date(r["created_at"]))
        date_iso = html.escape(r["created_at"])
        haystack = html.escape(" ".join([r["title"], r["description"], r["kind"], r["slug"], date_disp]).lower())
        rows.append(
            f'<tr data-search="{haystack}">'
            f'<td class="date"><time datetime="{date_iso}">{date_disp}</time></td>'
            f'<td class="title"><a href="{html.escape(url)}">{title}</a>'
            f'<span class="kind">{kind}</span></td>'
            f'<td class="desc">{desc}</td>'
            "</tr>"
        )
    body_rows = "\n".join(rows) or '<tr class="empty"><td colspan="3">No artifacts yet.</td></tr>'
    count = len(records)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>Scratchpad Artifacts</title>
<style>
:root{{color-scheme:light}}
*{{box-sizing:border-box}}
body{{margin:0;background:#ffffff;color:#1f2328;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;line-height:1.5}}
header{{position:sticky;top:0;background:rgba(255,255,255,.94);backdrop-filter:blur(6px);border-bottom:1px solid #d0d7de;padding:1rem 1.3rem}}
h1{{margin:0 0 .6rem;font-size:1.4rem}}
.meta{{color:#656d76;font-size:.82rem;margin-bottom:.7rem}}
#q{{width:100%;max-width:520px;padding:.5rem .7rem;font-size:.95rem;border:1px solid #d0d7de;border-radius:8px}}
main{{max-width:1000px;margin:0 auto;padding:1rem 1.3rem 4rem}}
table{{border-collapse:collapse;width:100%}}
td{{border-bottom:1px solid #eaecef;padding:.6rem .5rem;vertical-align:top}}
td.date{{white-space:nowrap;color:#656d76;font-size:.82rem;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;width:9.5rem}}
td.title a{{color:#0969da;text-decoration:none;font-weight:600}}
td.title a:hover{{text-decoration:underline}}
.kind{{display:inline-block;margin-left:.5rem;font-size:.68rem;color:#656d76;background:#f6f8fa;border:1px solid #d0d7de;border-radius:999px;padding:.05rem .45rem;vertical-align:middle}}
td.desc{{color:#3b4148;font-size:.9rem}}
tr.empty td{{color:#656d76;text-align:center;padding:2rem}}
tr.hidden{{display:none}}
footer{{color:#656d76;font-size:.75rem;padding:1rem 1.3rem;border-top:1px solid #eaecef;max-width:1000px;margin:0 auto}}
</style></head>
<body>
<header>
  <h1>Scratchpad Artifacts</h1>
  <div class="meta"><span id="count">{count}</span> artifact(s) · newest first · generated {generated} · tailnet-only</div>
  <input id="q" type="search" placeholder="Filter by title, description, kind, or date…" autocomplete="off" autofocus>
</header>
<main>
<table><tbody id="rows">
{body_rows}
</tbody></table>
</main>
<footer>Served privately via the Tailscale scratchpad. Regenerated on every publish and on a daily timer.</footer>
<script>
(function(){{
  var q=document.getElementById('q'), rows=Array.prototype.slice.call(document.querySelectorAll('#rows tr[data-search]')), c=document.getElementById('count');
  function apply(){{
    var t=q.value.trim().toLowerCase(), n=0;
    rows.forEach(function(r){{
      var hit=!t||r.getAttribute('data-search').indexOf(t)>=0;
      r.classList.toggle('hidden',!hit); if(hit)n++;
    }});
    c.textContent=n;
  }}
  q.addEventListener('input',apply);
}})();
</script>
</body></html>
"""


def build_index(root: str | Path, ts_host: str = DEFAULT_TS_HOST) -> tuple[str, int]:
    """Return (index_html, artifact_count) for the scratchpad at ``root``."""
    records = collect_artifacts(Path(root))
    return render_index(records, ts_host), len(records)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the scratchpad artifact index.")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="scratchpad root dir")
    ap.add_argument("--ts-host", default=DEFAULT_TS_HOST, help="tailnet host for artifact URLs")
    ap.add_argument("--output", default=None, help="output path (default: <root>/index.html)")
    ap.add_argument("--print", action="store_true", dest="to_stdout", help="print to stdout instead of writing")
    args = ap.parse_args()

    root = Path(args.root)
    html_doc, count = build_index(root, args.ts_host)
    if args.to_stdout:
        print(html_doc)
        return 0

    out = Path(args.output) if args.output else root / INDEX_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_doc, encoding="utf-8")
    print(f"wrote {out} ({count} artifact(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
