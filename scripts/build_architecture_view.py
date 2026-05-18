#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Build the Architecture Canvas View HTML.

Reads YAML exhibit sources from docs/architecture-view/sources/, verifies each
`source:` pointer exists (and captures git-last-touched timestamps), inlines
vendored rough.js + mermaid.min.js, and emits a single self-contained HTML to:

  - docs/architecture-view/output/architecture-map.html  (canonical)
  - web-ui/public/architecture-map.html                  (dashboard mirror)

Run: `uv run scripts/build_architecture_view.py`
Verify-only: `uv run scripts/build_architecture_view.py --verify-only`

Exits non-zero if any source pointer is missing.

See: docs/02_Subsystems/Architecture_Canvas_View.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = REPO_ROOT / "docs" / "architecture-view" / "sources"
DRILLDOWNS_DIR = REPO_ROOT / "docs" / "architecture-view" / "drill_downs"
OUTPUT_DIR = REPO_ROOT / "docs" / "architecture-view" / "output"
WEBUI_MIRROR = REPO_ROOT / "web-ui" / "public" / "architecture-map.html"
VENDOR_DIR = REPO_ROOT / "docs" / "architecture-view" / "vendor"

ROUGH_URL = "https://cdn.jsdelivr.net/npm/roughjs@4.6.6/bundled/rough.min.js"
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"

GREEN = 30   # days
AMBER = 90   # days


@dataclass
class PointerStatus:
    path: str
    exists: bool
    last_touched: str | None  # ISO 8601
    days_ago: int | None

    @property
    def badge(self) -> str:
        if not self.exists:
            return "red"
        if self.days_ago is None:
            return "red"
        if self.days_ago <= GREEN:
            return "green"
        if self.days_ago <= AMBER:
            return "amber"
        return "red"


@dataclass
class BuildResult:
    exhibits: list[dict] = field(default_factory=list)
    pointer_statuses: dict[str, PointerStatus] = field(default_factory=dict)
    stale_count: int = 0
    missing_count: int = 0
    git_sha: str = ""
    generated_at: str = ""


def run_git(*args: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", *args],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return ""


def git_last_touched(rel_path: str) -> str | None:
    iso = run_git("log", "-1", "--format=%cI", "--", rel_path)
    return iso or None


def check_pointer(rel_path: str) -> PointerStatus:
    abs_path = REPO_ROOT / rel_path
    exists = abs_path.exists()
    last = git_last_touched(rel_path) if exists else None
    days = None
    if last:
        try:
            ts = dt.datetime.fromisoformat(last)
            now = dt.datetime.now(ts.tzinfo or dt.timezone.utc)
            days = (now - ts).days
        except Exception:
            days = None
    return PointerStatus(path=rel_path, exists=exists, last_touched=last, days_ago=days)


def vendor_asset(url: str, dest: Path) -> str:
    """Download asset to vendor dir if missing, return its contents."""
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        print(f"  vendoring: {url} -> {dest.name}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                dest.write_bytes(r.read())
        except Exception as e:  # noqa: BLE001
            print(f"  WARN: vendoring failed ({e}); will use CDN <script src=> fallback")
            return ""
    return dest.read_text(encoding="utf-8")


def load_exhibits() -> list[dict]:
    exhibits = []
    for path in sorted(SOURCES_DIR.glob("*.yaml")):
        with path.open() as f:
            data = yaml.safe_load(f)
        data["_source_yaml"] = str(path.relative_to(REPO_ROOT))
        exhibits.append(data)
    return exhibits


def load_drilldowns() -> dict[str, str]:
    dd = {}
    for path in sorted(DRILLDOWNS_DIR.glob("*.mmd")):
        dd[path.stem] = path.read_text(encoding="utf-8")
    return dd


def verify_pointers(exhibits: list[dict]) -> tuple[dict[str, PointerStatus], int, int]:
    statuses: dict[str, PointerStatus] = {}
    stale = 0
    missing = 0

    def _record(src: str) -> None:
        nonlocal stale, missing
        if src in statuses:
            return
        s = check_pointer(src)
        statuses[src] = s
        if not s.exists:
            missing += 1
        elif s.badge in ("red", "amber"):
            stale += 1

    for ex in exhibits:
        # Top-level exhibit source: (E2/E3/E5/E6/E7 style)
        for src in ex.get("source", []) or []:
            _record(src)
        # Per-node sources (E1 style)
        for n in ex.get("nodes", []) or []:
            for src in n.get("source", []) or []:
                _record(src)
        # Per-band sources (E4 style)
        for b in ex.get("bands", []) or []:
            for src in b.get("source", []) or []:
                _record(src)
    return statuses, stale, missing


# ---------- HTML rendering ---------------------------------------------------

PALETTE = {
    "bg": "#fbf9f4",
    "ink": "#222222",
    "muted": "#666",
    "rule": "#d8d1bf",
    "card": "#ffffff",
    "accent": "#5b6bd4",
    "warm": "#d4a44e",
    "cool": "#7fb8d4",
    "coral": "#c47d6e",
    "green": "#3fae6a",
    "amber": "#d4a44e",
    "red": "#c44e4e",
}


def badge_html(status: PointerStatus) -> str:
    color = PALETTE[status.badge]
    if not status.exists:
        label = "missing"
    elif status.days_ago is None:
        label = "unknown"
    elif status.days_ago == 0:
        label = "today"
    else:
        label = f"{status.days_ago}d"
    return (
        f'<span class="badge" style="background:{color}" '
        f'title="{html.escape(status.path)} — {label}">{label}</span>'
    )


def render_hero(exhibit: dict, statuses: dict[str, PointerStatus]) -> str:
    """The Task Flow-Spine — rough.js draws shapes client-side from this JSON spec."""
    # Fixed layout: 5 stages laid out horizontally
    nodes = exhibit.get("nodes", [])
    if not nodes:
        return "<div class='exhibit'>no flow-spine nodes</div>"
    # Position each node along x; rough.js draws boxes + arrows
    n = len(nodes)
    spec_nodes = []
    # Per-node widths; "VPs / Cody" needs more room for its subtitle
    default_w, box_h = 180, 120
    wide_ids = {"vps_cody"}
    gap = 70
    x_origin = 20
    y_origin = 40
    cursor_x = x_origin
    for i, node in enumerate(nodes):
        w = 210 if node["id"] in wide_ids else default_w
        spec_nodes.append({
            "id": node["id"],
            "label": node["label"],
            "subtitle": node.get("subtitle", ""),
            "x": cursor_x,
            "y": y_origin,
            "w": w,
            "h": box_h,
        })
        cursor_x += w + gap
    canvas_w = cursor_x - gap + 20
    canvas_h = box_h + 180  # extra room for the loopback arc below the row

    # Build edges from YAML; map node ids to positions to derive arrow start/end
    id_to_box = {sn["id"]: sn for sn in spec_nodes}
    spec_edges = []
    for e in exhibit.get("edges", []) or []:
        src = id_to_box.get(e["from"])
        dst = id_to_box.get(e["to"])
        if not src or not dst:
            continue
        # If destination is to the left of source, render as a loopback arc
        # below the row of boxes. Otherwise, straight horizontal arrow.
        is_loopback = dst["x"] < src["x"]
        if is_loopback:
            x1 = src["x"] + src["w"] // 2
            y1 = src["y"] + src["h"]
            x2 = dst["x"] + dst["w"] // 2
            y2 = dst["y"] + dst["h"]
        else:
            x1 = src["x"] + src["w"]
            y1 = src["y"] + src["h"] // 2
            x2 = dst["x"]
            y2 = dst["y"] + dst["h"] // 2
        spec_edges.append({
            "from": e["from"],
            "to": e["to"],
            "label": e.get("label", ""),
            "style": e.get("style", "solid"),
            "loopback": is_loopback,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
        })

    # Drawer payload: source pointers (rendered to badges), blurb, canonical doc
    drawer = {}
    for node in nodes:
        pointers = []
        for src in node.get("source", []) or []:
            s = statuses[src]
            pointers.append({
                "path": src,
                "badge": s.badge,
                "days_ago": s.days_ago,
                "exists": s.exists,
            })
        drawer[node["id"]] = {
            "label": node["label"],
            "blurb": node.get("blurb", ""),
            "canonical_doc": node.get("canonical_doc", ""),
            "canonical_anchor": node.get("canonical_anchor", ""),
            "drilldown": node.get("drilldown", ""),
            "drilldown_id": Path(node.get("drilldown", "")).stem if node.get("drilldown") else "",
            "pointers": pointers,
        }

    spec_json = json.dumps({
        "nodes": spec_nodes,
        "edges": spec_edges,
        "canvas_w": canvas_w,
        "canvas_h": canvas_h,
        "drawer": drawer,
    })

    return f"""
<section class="exhibit hero" id="exhibit-{exhibit['id']}">
  <header class="ex-header">
    <h2>{html.escape(exhibit['title'])}</h2>
    <p class="ex-desc">{html.escape(exhibit['description'].strip())}</p>
  </header>
  <div class="rough-hero" data-spec='{html.escape(spec_json)}'></div>
</section>
"""


def render_claude_envs(exhibit: dict, statuses: dict[str, PointerStatus]) -> str:
    bands = exhibit.get("bands", [])
    band_html_parts = []
    for b in bands:
        pointers_html = "".join(
            f'<li><code>{html.escape(p)}</code> {badge_html(statuses[p])}</li>'
            for p in (b.get("source", []) or [])
        )
        runs_html = "".join(f"<li>{html.escape(r)}</li>" for r in b.get("runs", []))
        band_html_parts.append(f"""
        <article class="claude-band" style="--band:{b['color']}">
          <header>
            <h3>{html.escape(b['label'])}</h3>
            <div class="band-meta">
              <span class="endpoint">{html.escape(b['endpoint'])}</span>
              <span class="auth">{html.escape(b['auth'])}</span>
              <span class="models">{html.escape(b['models'])}</span>
            </div>
          </header>
          <p class="band-blurb">{html.escape(b['blurb'].strip())}</p>
          <details>
            <summary>Runs here</summary>
            <ul class="runs">{runs_html}</ul>
          </details>
          <details>
            <summary>Source pointers</summary>
            <ul class="pointers">{pointers_html}</ul>
          </details>
        </article>
        """)

    principles_html = "".join(
        f'<li>{html.escape(p)}</li>' for p in exhibit.get("key_principles", [])
    )

    return f"""
<section class="exhibit envs" id="exhibit-{exhibit['id']}">
  <header class="ex-header">
    <h2>{html.escape(exhibit['title'])}</h2>
    <p class="ex-desc">{html.escape(exhibit['description'].strip())}</p>
  </header>
  <div class="bands">{''.join(band_html_parts)}</div>
  <aside class="principles">
    <h4>Boundary invariants</h4>
    <ul>{principles_html}</ul>
  </aside>
</section>
"""


def render_glossary_legend(exhibit: dict, build: BuildResult) -> str:
    glossary_html = "".join(
        f'<dt>{html.escape(g["term"])}</dt><dd>{html.escape(g["definition"])}</dd>'
        for g in exhibit.get("glossary", [])
    )
    fresh = exhibit.get("legend", {}).get("freshness", [])
    fresh_html = "".join(
        f'<li><span class="dot" style="background:{f["color"]}"></span> '
        f'<b>{html.escape(f["label"])}</b> — {html.escape(f["meaning"])}</li>'
        for f in fresh
    )
    edges = exhibit.get("legend", {}).get("edges", [])
    edge_html = "".join(
        f'<li><span class="edge-{e["style"]}"></span> <b>{e["style"]}</b> — {html.escape(e["meaning"])}</li>'
        for e in edges
    )
    hints_html = "".join(
        f'<li>{html.escape(h)}</li>'
        for h in exhibit.get("legend", {}).get("click_hints", [])
    )

    return f"""
<section class="exhibit footer-rail" id="exhibit-{exhibit['id']}">
  <div class="rail-cell glossary">
    <h3>Glossary</h3>
    <dl>{glossary_html}</dl>
  </div>
  <div class="rail-cell legend">
    <h3>Legend</h3>
    <h4>Freshness</h4>
    <ul class="legend-list">{fresh_html}</ul>
    <h4>Edges</h4>
    <ul class="legend-list">{edge_html}</ul>
    <h4>Interaction</h4>
    <ul class="legend-list">{hints_html}</ul>
    <h4>Build</h4>
    <ul class="legend-list build-meta">
      <li>Generated: <code>{html.escape(build.generated_at)}</code></li>
      <li>Git SHA: <code>{html.escape(build.git_sha or 'unknown')}</code></li>
      <li>Stale pointers: <code>{build.stale_count}</code></li>
      <li>Missing pointers: <code>{build.missing_count}</code></li>
    </ul>
  </div>
</section>
"""


def _render_exhibit_pointers(exhibit: dict, statuses: dict[str, PointerStatus]) -> str:
    """Inline pointer block used by mermaid_panel / html_grid / html_list exhibits."""
    sources = exhibit.get("source", []) or []
    if not sources:
        return ""
    items = "".join(
        f'<li><code>{html.escape(p)}</code> {badge_html(statuses[p])}</li>'
        for p in sources
    )
    doc = exhibit.get("canonical_doc", "")
    doc_link = (
        f'<a class="canonical-link" href="../../{doc}" target="_blank">{html.escape(doc)}</a>'
        if doc else ""
    )
    return f"""
    <details class="exhibit-pointers">
      <summary>Source pointers · canonical doc</summary>
      <ul class="pointers">{items}</ul>
      {doc_link}
    </details>
    """


def render_mermaid_panel(exhibit: dict, statuses: dict[str, PointerStatus]) -> str:
    mermaid_src = (exhibit.get("mermaid") or "").strip()
    area = exhibit.get("grid_area", "")
    return f"""
<section class="exhibit mermaid-panel area-{area}" id="exhibit-{exhibit['id']}">
  <header class="ex-header">
    <h2>{html.escape(exhibit['title'])}</h2>
    <p class="ex-desc">{html.escape(exhibit['description'].strip())}</p>
  </header>
  <div class="mermaid-frame">
    <div class="mermaid">{html.escape(mermaid_src)}</div>
  </div>
  {_render_exhibit_pointers(exhibit, statuses)}
</section>
"""


def render_html_grid(exhibit: dict, statuses: dict[str, PointerStatus]) -> str:
    items = exhibit.get("inputs", [])
    cards = "".join(
        f"""
        <article class="input-card" style="--accent:{i.get('color','#7fb8d4')}">
          <header>
            <h4>{html.escape(i['label'])}</h4>
            <span class="kind">{html.escape(i.get('kind',''))}</span>
          </header>
          <p class="trigger"><b>Trigger:</b> {html.escape(i.get('trigger',''))}</p>
          <p class="handler"><b>Handler:</b> <code>{html.escape(i.get('handler',''))}</code></p>
          <p class="transform">{html.escape(i.get('transform',''))}</p>
        </article>
        """
        for i in items
    )
    area = exhibit.get("grid_area", "")
    return f"""
<section class="exhibit input-grid area-{area}" id="exhibit-{exhibit['id']}">
  <header class="ex-header">
    <h2>{html.escape(exhibit['title'])}</h2>
    <p class="ex-desc">{html.escape(exhibit['description'].strip())}</p>
  </header>
  <div class="inputs-grid-inner">{cards}</div>
  {_render_exhibit_pointers(exhibit, statuses)}
</section>
"""


def render_html_list(exhibit: dict, statuses: dict[str, PointerStatus]) -> str:
    surfaces = exhibit.get("surfaces", [])
    rows = "".join(
        f"""
        <li class="surface-row {'self-row' if s.get('self') else ''}">
          <span class="surface-name">{html.escape(s['name'])}</span>
          <code class="surface-route">{html.escape(s.get('route',''))}</code>
          <span class="surface-purpose">{html.escape(s.get('purpose',''))}</span>
          {'<span class="hq-pill">HQ</span>' if s.get('hq') else ''}
        </li>
        """
        for s in surfaces
    )
    area = exhibit.get("grid_area", "")
    return f"""
<section class="exhibit surface-list area-{area}" id="exhibit-{exhibit['id']}">
  <header class="ex-header">
    <h2>{html.escape(exhibit['title'])}</h2>
    <p class="ex-desc">{html.escape(exhibit['description'].strip())}</p>
  </header>
  <ul class="surfaces">{rows}</ul>
  {_render_exhibit_pointers(exhibit, statuses)}
</section>
"""


def render_drilldown_templates(drilldowns: dict[str, str]) -> str:
    parts = []
    for stem, content in drilldowns.items():
        parts.append(
            f'<template id="drilldown-{stem}"><div class="mermaid">'
            f'{html.escape(content)}'
            f'</div></template>'
        )
    return "\n".join(parts)


# ---------- Page assembly ----------------------------------------------------

CSS = """
:root {
  --bg: #fbf9f4;
  --ink: #222;
  --muted: #666;
  --rule: #e3ddc9;
  --card: #ffffff;
  --accent: #5b6bd4;
  --warm: #d4a44e;
  --cool: #7fb8d4;
  --coral: #c47d6e;
  --green: #3fae6a;
  --amber: #d4a44e;
  --red: #c44e4e;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", "Helvetica Neue", sans-serif;
  background: var(--bg);
  color: var(--ink);
  font-size: 14px;
  line-height: 1.5;
}
.page-header {
  border-bottom: 1px solid var(--rule);
  padding: 14px 28px;
  display: flex;
  align-items: baseline;
  gap: 24px;
  background: #fff;
}
.page-header h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.page-header .subtitle {
  color: var(--muted);
  font-size: 13px;
}
.page-header .build-pill {
  margin-left: auto;
  font-size: 12px;
  color: var(--muted);
  font-family: ui-monospace, monospace;
}
.page-header .build-pill .stale {
  background: var(--amber);
  color: #fff;
  padding: 2px 7px;
  border-radius: 8px;
  margin-left: 6px;
}
.page-header .build-pill .stale.zero {
  background: var(--green);
}

.canvas {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  grid-auto-rows: auto;
  gap: 18px;
  padding: 18px;
  max-width: 2000px;
  margin: 0 auto;
}
.canvas .hero       { grid-column: 1 / -1; }
.canvas .footer-rail { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 18px; }
.canvas .area-mid_left    { grid-column: 1; }
.canvas .area-mid_center  { grid-column: 2; }
.canvas .area-mid_right   { grid-column: 3; }
.canvas .area-lower_left  { grid-column: 1; }
.canvas .area-lower_center { grid-column: 2; }
.canvas .area-lower_right { grid-column: 3; }

/* Each exhibit cell uses full available height for tidy alignment */
.canvas .exhibit { align-self: stretch; }

/* ----- Mermaid panel (E2 / E3 / E7) ----- */
.mermaid-panel .mermaid-frame {
  background: var(--bg);
  border: 1px solid var(--rule);
  border-radius: 8px;
  padding: 10px;
  overflow: auto;
  max-height: 520px;
}
.mermaid-panel .mermaid {
  text-align: center;
}
.mermaid-panel .mermaid svg {
  max-width: 100%;
  height: auto;
}

/* ----- Inputs grid (E5) ----- */
.input-grid .inputs-grid-inner {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
  margin-top: 4px;
}
.input-card {
  border: 1px solid var(--rule);
  border-left: 3px solid var(--accent);
  border-radius: 6px;
  padding: 9px 11px;
  background: linear-gradient(180deg, #fdfbf5 0%, #f9f5ea 100%);
  font-size: 12px;
}
.input-card { border-left-color: var(--accent); }
.input-card { border-left: 3px solid var(--accent); }
.input-card[style*="--accent"] { border-left-color: var(--accent); }
.input-card header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 4px;
  gap: 6px;
}
.input-card h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
}
.input-card .kind {
  font-size: 9.5px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  font-family: ui-monospace, monospace;
}
.input-card p {
  margin: 2px 0;
  font-size: 11.5px;
  color: var(--ink);
}
.input-card code {
  font-size: 11px;
  background: rgba(0,0,0,0.04);
  padding: 0 4px;
  border-radius: 3px;
}
.input-card .transform { color: var(--muted); margin-top: 4px; font-style: italic; }
.input-card[style*="--accent"] { border-left: 3px solid var(--accent); }
.input-card { border-left: 3px solid var(--accent, #5b6bd4); }

/* ----- Surfaces list (E6) ----- */
.surface-list .surfaces {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 520px;
  overflow-y: auto;
}
.surface-row {
  display: grid;
  grid-template-columns: 160px 1fr auto;
  gap: 10px;
  align-items: baseline;
  padding: 6px 8px;
  border-bottom: 1px dashed rgba(0,0,0,0.05);
  font-size: 12px;
}
.surface-row .surface-name {
  font-weight: 600;
  color: var(--ink);
  font-size: 12.5px;
}
.surface-row .surface-route {
  font-family: ui-monospace, monospace;
  font-size: 11px;
  color: var(--accent);
  grid-column: 1 / 2;
}
.surface-row {
  grid-template-columns: minmax(160px, max-content) 1fr auto;
}
.surface-row .surface-purpose {
  font-size: 11.5px;
  color: var(--muted);
  grid-column: 2 / 3;
}
.surface-row .hq-pill {
  background: var(--warm);
  color: #fff;
  font-size: 9px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 6px;
  letter-spacing: 0.04em;
  align-self: center;
}
.surface-row.self-row { background: linear-gradient(90deg, rgba(91,107,212,0.06) 0%, transparent 100%); }
.surface-row.self-row .surface-name::after { content: " ★"; color: var(--accent); }

/* Common: exhibit-level pointers details */
.exhibit-pointers {
  margin-top: 10px;
  border-top: 1px dashed var(--rule);
  padding-top: 8px;
  font-size: 12px;
}
.exhibit-pointers > summary {
  cursor: pointer;
  font-size: 10.5px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--muted);
}
.exhibit-pointers .pointers {
  list-style: none;
  margin: 6px 0 4px;
  padding: 0;
  font-family: ui-monospace, monospace;
  font-size: 11.5px;
}
.exhibit-pointers .pointers li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0;
}
.exhibit-pointers .canonical-link {
  display: inline-block;
  margin-top: 4px;
  color: var(--accent);
  text-decoration: none;
  font-size: 11.5px;
}
.exhibit-pointers .canonical-link:hover { text-decoration: underline; }

.missing-exhibit {
  background: rgba(196, 78, 78, 0.05);
  border: 1px dashed var(--red);
  border-radius: 10px;
  padding: 18px;
  font-family: ui-monospace, monospace;
  color: var(--red);
}

.exhibit {
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 10px;
  padding: 18px 22px;
  box-shadow: 0 1px 0 rgba(0,0,0,0.02), 0 4px 18px rgba(0,0,0,0.04);
}
.ex-header h2 {
  margin: 0 0 6px;
  font-size: 17px;
  font-weight: 600;
}
.ex-header .ex-desc {
  margin: 0 0 14px;
  color: var(--muted);
  font-size: 13px;
  white-space: pre-line;
}

.rough-hero {
  width: 100%;
  overflow-x: auto;
  padding: 6px 0;
}
.rough-hero svg { display: block; max-width: 100%; height: auto; }
.rough-hero .node-hit { cursor: pointer; outline: none; }
.rough-hero .node-hit:hover rect { filter: brightness(1.02); }
.rough-hero .node-hit:focus-visible rect { stroke: var(--accent); stroke-width: 2; }
.rough-hero .node-label {
  font-family: ui-sans-serif, system-ui, sans-serif;
  font-weight: 600;
  font-size: 15px;
  fill: var(--ink);
  pointer-events: none;
}
.rough-hero .node-sub {
  font-family: ui-sans-serif, system-ui, sans-serif;
  font-size: 11px;
  fill: var(--muted);
  pointer-events: none;
}
.rough-hero .edge-label {
  font-family: ui-monospace, monospace;
  font-size: 10px;
  fill: var(--muted);
  pointer-events: none;
}

/* Claude envs */
.envs .bands {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.claude-band {
  border-left: 4px solid var(--band, var(--accent));
  background: color-mix(in srgb, var(--band, var(--accent)) 8%, white);
  border-radius: 6px;
  padding: 12px 14px;
}
.claude-band h3 {
  margin: 0 0 4px;
  font-size: 14px;
}
.claude-band .band-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 11px;
  color: var(--muted);
  font-family: ui-monospace, monospace;
  margin-bottom: 6px;
}
.claude-band .band-blurb {
  margin: 6px 0 8px;
  font-size: 12.5px;
}
.claude-band details {
  font-size: 12px;
  margin-top: 4px;
}
.claude-band summary {
  cursor: pointer;
  color: var(--muted);
  font-weight: 500;
}
.claude-band ul {
  margin: 6px 0 0;
  padding-left: 18px;
}
.envs .principles {
  margin-top: 14px;
  border-top: 1px dashed var(--rule);
  padding-top: 10px;
  font-size: 12.5px;
}
.envs .principles h4 {
  margin: 0 0 6px;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
}
.envs .principles ul {
  margin: 0;
  padding-left: 18px;
}

/* Footer rail */
.footer-rail {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: 18px;
}
.rail-cell h3 {
  margin: 0 0 10px;
  font-size: 14px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
}
.rail-cell h4 {
  margin: 12px 0 4px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
}
.glossary dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  column-gap: 16px;
  row-gap: 4px;
  margin: 0;
  font-size: 13px;
}
.glossary dt {
  font-weight: 600;
  color: var(--ink);
}
.glossary dd {
  margin: 0;
  color: var(--muted);
}
.legend-list {
  list-style: none;
  padding: 0;
  margin: 0;
  font-size: 12.5px;
}
.legend-list li { margin: 4px 0; display: flex; align-items: center; gap: 8px; }
.legend-list .dot {
  display: inline-block;
  width: 12px; height: 12px;
  border-radius: 50%;
}
.legend-list .edge-solid, .legend-list .edge-dashed {
  display: inline-block;
  width: 28px; height: 2px;
  background: var(--ink);
}
.legend-list .edge-dashed {
  background: transparent;
  border-top: 2px dashed var(--ink);
}
.build-meta li { color: var(--muted); }
.build-meta code { background: var(--bg); padding: 1px 5px; border-radius: 3px; }

/* Drawer */
.drawer-backdrop {
  position: fixed; inset: 0;
  background: rgba(15, 15, 25, 0.45);
  opacity: 0;
  pointer-events: none;
  transition: opacity 200ms ease;
  z-index: 50;
}
.drawer-backdrop.open { opacity: 1; pointer-events: auto; }
.drawer {
  position: fixed;
  top: 0; right: 0; bottom: 0;
  width: 48%;
  max-width: 820px;
  min-width: 420px;
  background: #fff;
  box-shadow: -10px 0 30px rgba(0,0,0,0.12);
  transform: translateX(100%);
  transition: transform 240ms cubic-bezier(0.2, 0.7, 0.2, 1);
  z-index: 51;
  display: flex;
  flex-direction: column;
}
.drawer.open { transform: translateX(0); }
.drawer-header {
  padding: 14px 22px;
  border-bottom: 1px solid var(--rule);
  display: flex;
  align-items: center;
  gap: 14px;
}
.drawer-header h2 {
  margin: 0;
  font-size: 17px;
}
.drawer-header .close-btn {
  margin-left: auto;
  background: none;
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 4px 10px;
  cursor: pointer;
  font-size: 13px;
}
.drawer-body {
  padding: 18px 22px;
  overflow-y: auto;
  flex: 1;
}
.drawer .blurb {
  margin: 0 0 16px;
  color: var(--muted);
  white-space: pre-line;
}
.drawer .pointers-block {
  background: var(--bg);
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 10px 14px;
  margin: 14px 0;
}
.drawer .pointers-block h4 {
  margin: 0 0 6px;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
}
.drawer .pointers-block ul {
  list-style: none;
  padding: 0;
  margin: 0;
  font-size: 12.5px;
  font-family: ui-monospace, monospace;
}
.drawer .pointers-block li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
}
.drawer .canonical-link {
  display: inline-block;
  margin-top: 4px;
  color: var(--accent);
  text-decoration: none;
  font-size: 13px;
}
.drawer .canonical-link:hover { text-decoration: underline; }
.drawer .mermaid {
  background: var(--bg);
  border: 1px solid var(--rule);
  border-radius: 6px;
  padding: 14px;
  margin: 8px 0;
  text-align: center;
}

.badge {
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  color: #fff;
  padding: 1px 6px;
  border-radius: 8px;
  letter-spacing: 0.02em;
  vertical-align: middle;
}
"""


HERO_JS = r"""
(function () {
  const heroEl = document.querySelector('.rough-hero');
  if (!heroEl) return;
  const spec = JSON.parse(heroEl.dataset.spec);
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${spec.canvas_w} ${spec.canvas_h}`);
  svg.setAttribute('xmlns', svgNS);
  svg.setAttribute('width', spec.canvas_w);
  svg.setAttribute('height', spec.canvas_h);
  heroEl.appendChild(svg);

  const rc = window.rough ? rough.svg(svg) : null;

  const fills = ['#fef3d5', '#e8f4fa', '#fbe8d9', '#e0efd9', '#ecd9f4'];

  spec.nodes.forEach((n, i) => {
    const g = document.createElementNS(svgNS, 'g');
    g.classList.add('node-hit');
    g.dataset.nodeId = n.id;
    g.setAttribute('tabindex', '0');
    g.setAttribute('role', 'button');
    g.setAttribute('aria-label', 'Open ' + n.label + ' details');
    if (rc) {
      const rect = rc.rectangle(n.x, n.y, n.w, n.h, {
        roughness: 1.6,
        fill: fills[i % fills.length],
        fillStyle: 'hachure',
        hachureGap: 6,
        stroke: '#333',
        strokeWidth: 1.3,
        seed: 100 + i,
      });
      g.appendChild(rect);
    } else {
      // Fallback: plain rect
      const r = document.createElementNS(svgNS, 'rect');
      r.setAttribute('x', n.x);
      r.setAttribute('y', n.y);
      r.setAttribute('width', n.w);
      r.setAttribute('height', n.h);
      r.setAttribute('fill', fills[i % fills.length]);
      r.setAttribute('stroke', '#333');
      r.setAttribute('stroke-width', '1.3');
      g.appendChild(r);
    }
    const tx = document.createElementNS(svgNS, 'text');
    tx.setAttribute('x', n.x + n.w / 2);
    tx.setAttribute('y', n.y + n.h / 2 - 10);
    tx.setAttribute('text-anchor', 'middle');
    tx.setAttribute('class', 'node-label');
    tx.textContent = n.label;
    g.appendChild(tx);
    if (n.subtitle) {
      const st = document.createElementNS(svgNS, 'text');
      st.setAttribute('x', n.x + n.w / 2);
      st.setAttribute('y', n.y + n.h / 2 + 12);
      st.setAttribute('text-anchor', 'middle');
      st.setAttribute('class', 'node-sub');
      st.textContent = n.subtitle;
      g.appendChild(st);
    }
    svg.appendChild(g);
    const fire = () => openDrawer(n.id, spec.drawer[n.id]);
    g.addEventListener('click', fire);
    g.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fire(); }
    });
  });

  spec.edges.forEach((e, i) => {
    let path, headPts, labelX, labelY;
    if (e.loopback) {
      // Bow-down cubic curve from (x1, y1) down to a depth, back up to (x2, y2)
      const depth = 90;
      const c1x = e.x1, c1y = e.y1 + depth;
      const c2x = e.x2, c2y = e.y2 + depth;
      // End the path slightly before the box so the arrowhead points at it
      path = `M ${e.x1} ${e.y1} C ${c1x} ${c1y}, ${c2x} ${c2y}, ${e.x2} ${e.y2 + 6}`;
      // Arrowhead points up at destination box's bottom edge
      headPts = `${e.x2},${e.y2} ${e.x2 - 5},${e.y2 + 9} ${e.x2 + 5},${e.y2 + 9}`;
      labelX = (e.x1 + e.x2) / 2;
      labelY = Math.max(e.y1, e.y2) + depth + 6;
    } else {
      path = `M ${e.x1} ${e.y1} L ${e.x2 - 8} ${e.y2}`;
      headPts = `${e.x2},${e.y2} ${e.x2 - 9},${e.y2 - 5} ${e.x2 - 9},${e.y2 + 5}`;
      labelX = (e.x1 + e.x2) / 2;
      labelY = (e.y1 + e.y2) / 2 - 4;
    }
    if (rc) {
      const line = rc.path(path, {
        roughness: 1.3,
        stroke: '#444',
        strokeWidth: 1.4,
        fill: 'none',
        strokeLineDash: e.style === 'dashed' ? [6, 5] : undefined,
        seed: 500 + i,
      });
      svg.appendChild(line);
    } else {
      const p = document.createElementNS(svgNS, 'path');
      p.setAttribute('d', path);
      p.setAttribute('stroke', '#444');
      p.setAttribute('fill', 'none');
      p.setAttribute('stroke-width', '1.4');
      if (e.style === 'dashed') p.setAttribute('stroke-dasharray', '6,5');
      svg.appendChild(p);
    }
    const head = document.createElementNS(svgNS, 'polygon');
    head.setAttribute('points', headPts);
    head.setAttribute('fill', '#444');
    svg.appendChild(head);
    if (e.label) {
      const lt = document.createElementNS(svgNS, 'text');
      lt.setAttribute('x', labelX);
      lt.setAttribute('y', labelY);
      lt.setAttribute('text-anchor', 'middle');
      lt.setAttribute('class', 'edge-label');
      if (e.loopback) lt.setAttribute('font-style', 'italic');
      lt.textContent = e.label;
      svg.appendChild(lt);
    }
  });
})();
"""


DRAWER_JS = r"""
function openDrawer(nodeId, data) {
  const drawer = document.getElementById('drawer');
  const backdrop = document.getElementById('drawer-backdrop');
  document.getElementById('drawer-title').textContent = data.label;
  document.getElementById('drawer-blurb').textContent = data.blurb;
  const ptrsEl = document.getElementById('drawer-pointers');
  ptrsEl.innerHTML = '';
  (data.pointers || []).forEach(p => {
    const li = document.createElement('li');
    const ageLabel = p.exists ? (p.days_ago === 0 ? 'today' : (p.days_ago != null ? p.days_ago + 'd' : 'unknown')) : 'missing';
    li.innerHTML = `<span class="badge" style="background: var(--${p.badge})">${ageLabel}</span> <code>${p.path}</code>`;
    ptrsEl.appendChild(li);
  });
  // Canonical doc link
  const docLink = document.getElementById('drawer-doc-link');
  if (data.canonical_doc) {
    docLink.textContent = `Open canonical doc: ${data.canonical_doc}${data.canonical_anchor || ''}`;
    docLink.href = `../../${data.canonical_doc}${data.canonical_anchor || ''}`;
    docLink.style.display = 'inline-block';
  } else {
    docLink.style.display = 'none';
  }
  // Mermaid drill-down
  const mEl = document.getElementById('drawer-mermaid');
  mEl.innerHTML = '';
  if (data.drilldown_id) {
    const tpl = document.getElementById('drilldown-' + data.drilldown_id);
    if (tpl) {
      const node = tpl.content.cloneNode(true);
      mEl.appendChild(node);
      if (window.mermaid && mermaid.run) {
        mermaid.run({ querySelector: '#drawer-mermaid .mermaid' });
      }
    }
  }
  drawer.classList.add('open');
  backdrop.classList.add('open');
}
function closeDrawer() {
  document.getElementById('drawer').classList.remove('open');
  document.getElementById('drawer-backdrop').classList.remove('open');
}
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('drawer-backdrop').addEventListener('click', closeDrawer);
  document.getElementById('drawer-close').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
  if (window.mermaid) {
    mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'loose' });
    // Render every page-level Mermaid panel (E2/E3/E7). Drawer-internal
    // diagrams live inside #drawer-mermaid and are rendered lazily when the
    // drawer opens (see openDrawer above) — exclude them here to avoid
    // double-processing.
    try {
      mermaid.run({ querySelector: '.mermaid-panel .mermaid' });
    } catch (e) {
      console.error('mermaid.run failed for page-level diagrams', e);
    }
  }
});
"""


def render_html(exhibits: list[dict], drilldowns: dict[str, str], build: BuildResult, rough_js: str, mermaid_js: str) -> str:
    by_id = {e["id"]: e for e in exhibits}

    def _section(eid: str, render_fn) -> str:
        if eid not in by_id:
            return f"<div class='exhibit missing-exhibit'>missing {eid}</div>"
        return render_fn(by_id[eid], build.pointer_statuses)

    hero_html = _section("e01_flow_spine", render_hero)
    intel_html = _section("e02_intelligence", render_mermaid_panel)
    knowledge_html = _section("e03_knowledge", render_mermaid_panel)
    envs_html = _section("e04_claude_envs", render_claude_envs)
    inputs_html = _section("e05_inputs", render_html_grid)
    surfaces_html = _section("e06_surfaces", render_html_list)
    ops_html = _section("e07_ops", render_mermaid_panel)
    glossary_html = render_glossary_legend(by_id["e08_glossary_legend"], build) if "e08_glossary_legend" in by_id else "<div class='exhibit'>missing e08_glossary_legend</div>"

    _unused_legacy_placeholder = """
<section class="exhibit placeholder">
  <header class="ex-header">
    <h2>Roadmap — Phases 2 & 3</h2>
    <p class="ex-desc">Six more exhibits will fill out the canvas. v1 ships the hero, Claude environments, glossary, and the verified-pointer pipeline.
See <code>docs/02_Subsystems/Architecture_Canvas_View.md</code> §4 for the phased plan.</p>
  </header>
  <div class="roadmap-grid">
    <article class="roadmap-card phase-2">
      <span class="phase-tag">Phase 2</span>
      <h4>E2 — Intelligence Pipeline</h4>
      <p>CSI / ClaudeDevs X intel — official polling, vault population, demo workspaces.</p>
      <span class="renderer">Mermaid</span>
    </article>
    <article class="roadmap-card phase-2">
      <span class="phase-tag">Phase 2</span>
      <h4>E3 — Knowledge Plane</h4>
      <p>LLM Wiki + Memory + Vault relationships. Append-dominant Memex primitives.</p>
      <span class="renderer">Mermaid</span>
    </article>
    <article class="roadmap-card phase-3">
      <span class="phase-tag">Phase 3</span>
      <h4>E5 — Inputs Catalog</h4>
      <p>Email, Discord, Telegram, webhooks, cron — every ingress channel at a glance.</p>
      <span class="renderer">HTML grid</span>
    </article>
    <article class="roadmap-card phase-3">
      <span class="phase-tag">Phase 3</span>
      <h4>E6 — Operating Surfaces</h4>
      <p>Mission Control, Task Hub Dashboard, ledger, events — what surfaces consume the system.</p>
      <span class="renderer">HTML list</span>
    </article>
    <article class="roadmap-card phase-3">
      <span class="phase-tag">Phase 3</span>
      <h4>E7 — Ops / Ship Pipeline</h4>
      <p>Branches → PR → auto-merge → deploy.yml → VPS. Auto-merge allowlist visualised.</p>
      <span class="renderer">Mermaid</span>
    </article>
    <article class="roadmap-card phase-4">
      <span class="phase-tag">Phase 4</span>
      <h4>Wiring</h4>
      <p>Dashboard link from Mission Control, weekly drift cron, pre-commit hook on the build script.</p>
      <span class="renderer">Operational</span>
    </article>
  </div>
</section>
"""

    drilldowns_tpl = render_drilldown_templates(drilldowns)
    stale_class = "zero" if build.stale_count == 0 else ""

    # Script tags: prefer inline vendored, fall back to CDN
    rough_tag = f'<script>{rough_js}</script>' if rough_js else f'<script src="{ROUGH_URL}"></script>'
    mermaid_tag = f'<script>{mermaid_js}</script>' if mermaid_js else f'<script src="{MERMAID_URL}"></script>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>UA Architecture Canvas</title>
<meta name="viewport" content="width=1400">
<style>{CSS}</style>
</head>
<body>
<header class="page-header">
  <h1>Universal Agent — Architecture Canvas</h1>
  <span class="subtitle">Phase 1+2+3 · Flow-Spine · Intelligence · Knowledge · Envs · Inputs · Surfaces · Ops · Glossary</span>
  <span class="build-pill">
    {html.escape(build.generated_at)} · {html.escape(build.git_sha[:8] if build.git_sha else 'no-sha')}
    <span class="stale {stale_class}">stale: {build.stale_count} · missing: {build.missing_count}</span>
  </span>
</header>

<main class="canvas">
  {hero_html}
  {intel_html}
  {knowledge_html}
  {envs_html}
  {inputs_html}
  {surfaces_html}
  {ops_html}
  {glossary_html}
</main>

<div id="drawer-backdrop" class="drawer-backdrop"></div>
<aside id="drawer" class="drawer" aria-hidden="true">
  <header class="drawer-header">
    <h2 id="drawer-title">Stage</h2>
    <button id="drawer-close" class="close-btn">Close</button>
  </header>
  <div class="drawer-body">
    <p id="drawer-blurb" class="blurb"></p>
    <div class="pointers-block">
      <h4>Source pointers</h4>
      <ul id="drawer-pointers"></ul>
    </div>
    <a id="drawer-doc-link" class="canonical-link" href="#" target="_blank">Open canonical doc</a>
    <div id="drawer-mermaid"></div>
  </div>
</aside>

{drilldowns_tpl}

{rough_tag}
{mermaid_tag}
<script>{HERO_JS}</script>
<script>{DRAWER_JS}</script>
</body>
</html>
"""


# ---------- Main -------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true",
                        help="Validate pointers only; do not emit HTML.")
    parser.add_argument("--allow-stale", action="store_true",
                        help="Don't fail on amber/red pointers (still fails on missing).")
    args = parser.parse_args()

    print(f"[build] repo root: {REPO_ROOT}")
    print(f"[build] sources:   {SOURCES_DIR}")

    if not SOURCES_DIR.exists():
        print(f"ERROR: sources dir missing: {SOURCES_DIR}", file=sys.stderr)
        return 2

    exhibits = load_exhibits()
    print(f"[build] loaded {len(exhibits)} exhibit YAMLs")

    drilldowns = load_drilldowns()
    print(f"[build] loaded {len(drilldowns)} drill-down Mermaid files")

    statuses, stale, missing = verify_pointers(exhibits)
    print(f"[build] verified {len(statuses)} pointers — stale: {stale}, missing: {missing}")

    if missing:
        print("\n  MISSING POINTERS:", file=sys.stderr)
        for path, s in statuses.items():
            if not s.exists:
                print(f"    - {path}", file=sys.stderr)

    build = BuildResult(
        exhibits=exhibits,
        pointer_statuses=statuses,
        stale_count=stale,
        missing_count=missing,
        git_sha=run_git("rev-parse", "HEAD"),
        generated_at=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    if args.verify_only:
        if missing:
            return 1
        return 0

    # Vendor assets
    rough_js = vendor_asset(ROUGH_URL, VENDOR_DIR / "rough.min.js")
    mermaid_js = vendor_asset(MERMAID_URL, VENDOR_DIR / "mermaid.min.js")

    # Render
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html_out = render_html(exhibits, drilldowns, build, rough_js, mermaid_js)
    out_path = OUTPUT_DIR / "architecture-map.html"
    out_path.write_text(html_out, encoding="utf-8")
    size_kb = out_path.stat().st_size // 1024
    print(f"[build] wrote {out_path} ({size_kb} KB)")

    # Mirror into web-ui/public/ if it exists
    if WEBUI_MIRROR.parent.exists():
        shutil.copyfile(out_path, WEBUI_MIRROR)
        print(f"[build] mirrored to {WEBUI_MIRROR}")
    else:
        print(f"[build] skipped web-ui mirror (parent {WEBUI_MIRROR.parent} not found)")

    if missing and not args.allow_stale:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
