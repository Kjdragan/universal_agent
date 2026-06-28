---
name: publish-to-scratchpad
description: >
  Publish a rendered HTML report/analysis/diagram to the tailnet HTML scratchpad and
  hand the operator a clickable link, instead of pasting raw markdown or attaching a
  file. USE THIS SKILL whenever you produce ANY operator-facing report, digest, diff
  review, architecture diagram, plan recap, comparison table, or other HTML artifact
  that benefits from real rendering — styling, Mermaid/SVG diagrams, or (critically)
  working in-page table-of-contents anchors that let the reader jump to a section.
  Kevin runs Claude Code terminal-only and reads email in clients that strip links and
  flatten HTML/PDF attachments, so a markdown paste or a PDF attachment LOSES the
  styling and the clickable navigation. The scratchpad gives him a fully-rendered,
  privately-served page that works on every one of his devices. Trigger even when the
  user doesn't say the word "scratchpad" — phrases like "give me a link to that report",
  "show me the digest", "publish this analysis", "where can I view the rendered version",
  "send me something I can click through", or any moment you're about to hand over a
  report all mean: use this skill. Also reach for it proactively when you've just built
  an HTML page (e.g. via the visual-explainer skill) and need to surface it. It also
  renders markdown files/folders to styled HTML, publishes any single file (image, PDF,
  CSV, screenshot) for the operator to view without an IDE, and keeps a browsable,
  searchable artifact index of everything published. Do NOT use it for artifacts going
  to EXTERNAL recipients — the scratchpad is tailnet-only and the link is dead
  off-tailnet (attach a file or use a public host for those).
---

# Publish to the Tailnet HTML Scratchpad

## Why this exists

Kevin operates Claude Code **terminal-only** and reads his mail in clients that strip
hyperlinks and flatten HTML/PDF attachments. That breaks the two things that make a
report useful:

1. **Rendering** — styling, Mermaid/SVG diagrams, code blocks. A markdown paste in a
   terminal is raw text; a PDF loses interactivity.
2. **In-page navigation** — a table of contents whose entries *jump* to the matching
   section. PDFs only get a static bookmark outline; email can't do in-document anchors
   at all. Real `<a href="#anchor">` links only work in a live HTML page.

The scratchpad solves both. It's a `tailscale serve` path-mount on the VPS that exposes
an HTML file at a private URL reachable **only from Kevin's own tailnet devices**
(desktop, phone, tablet) — never the public internet. Tailnet membership *is* the auth
boundary. So you can publish a report and hand over a link that renders perfectly and
works everywhere he reads, with all anchors live.

The rule of thumb: **when you would otherwise paste markdown or attach a file, publish
to the scratchpad and hand over the link instead.**

## The mechanism (one command)

`scripts/publish_scratch.sh` is the single source of truth. It auto-detects whether it's
running on the VPS (writes directly to `/home/ua/ua_scratch`) or elsewhere on the tailnet
(copies over `ssh ua@uaonvps`) — you never pass a flag for that.

```bash
# Publish a single file (HTML, image, PDF, CSV, …). Prints ONLY the URL to stdout
# (status messages go to stderr), so you can capture it directly.
URL=$(scripts/publish_scratch.sh report.html)

# Optional readable slug names the subdir -> /scratch/<slug>/report.html
URL=$(scripts/publish_scratch.sh report.html my-analysis)

# Publish a whole directory tree under ONE slug (subdirs preserved). Use this for a
# rendered, cross-linked doc set — prints the base URL .../scratch/<slug>/.
BASE=$(scripts/publish_scratch.sh --dir ./rendered_site my-docs)

# Markdown AUTO-RENDERS (since 2026-06-22). Hand the shell a raw .md (or a --dir of
# them) and it publishes a styled, cross-linked HTML page — NOT raw source. So
# "show me this .md on the scratchpad" is just: URL=$(scripts/publish_scratch.sh doc.md)
URL=$(scripts/publish_scratch.sh notes.md)              # -> rendered HTML page
BASE=$(scripts/publish_scratch.sh --dir ./my_md_docs)   # -> rendered, cross-linked site

# Rebuild the browsable artifact index (also auto-runs after every publish + on a timer).
scripts/publish_scratch.sh --reindex

# One-time/idempotent setup of the /scratch mapping (rarely needed; already configured).
scripts/publish_scratch.sh --init

# Verify the serve mappings are intact (must still show /scratch live).
scripts/publish_scratch.sh --status
```

The printed URL looks like
`https://uaonvps.taildcc090.ts.net/scratch/<slug>/report.html`.

If you don't pass a slug, the script generates a random unguessable one. The slug is
hygiene (the URL is unlisted), not the real security boundary — that's tailnet
membership. Use a readable slug when a stable, shareable-within-the-tailnet path helps;
use the random default for one-off reports.

## From Python pipelines (cron scripts, services)

A deterministic Python pipeline (a cron digest, a service) can't "invoke a skill" — it
runs without an LLM in the loop. For those callers, use the helpers that wrap the
**same** `publish_scratch.sh`, so there's one mechanism and no drift. Four front doors,
all in `src/universal_agent/services/scratch_publish.py`:

```python
from universal_agent.services.scratch_publish import (
    publish_html_to_scratch,      # a rendered HTML string (the original)
    publish_markdown_to_scratch,  # a markdown string → styled, light-mode HTML page
    publish_file_to_scratch,      # any single file as-is (image, PDF, CSV, screenshot…)
    publish_docset_to_scratch,    # a folder of markdown + files → cross-linked HTML site
)

# Render markdown to a proper page (no need to hand-write HTML / force light mode yourself):
url = publish_markdown_to_scratch(md_text, slug="design", title="Design", description="…")

# Hand over a diagram/screenshot/data file the operator can open without an IDE:
url = publish_file_to_scratch("/path/chart.png", title="Latency chart", description="p95 over 24h")

# Publish a whole doc folder; links between docs are rewritten, a nav bar is added, and
# the returned URL points at the hub page (DESIGN.md/README.md, else the first markdown):
url = publish_docset_to_scratch("/path/to/docs", title="Receptionist design", description="…")

# The original HTML path (unchanged contract; used by the YouTube digest):
url = publish_html_to_scratch(html_str, slug="yt-digest-2026-06-02", filename="digest.html")
```

Every helper returns the URL string on success or `None` on failure (none raise for an
ordinary publish error), so callers can degrade gracefully — a raw-but-delivered fallback
always beats a dropped report. Pass `title`/`description` whenever you can: they populate
the artifact index (below) so the operator can find the artifact later by browsing rather
than digging up the link.

## The artifact index (browse everything)

Every published artifact lands in one collated, persistent store
(`/home/ua/ua_scratch/`, which survives deploys) and is listed on a single browsable,
**date-sorted, searchable** index page:

**`https://uaonvps.taildcc090.ts.net/scratch/`**

The index shows title · date · description per artifact and links straight to each one.
It is regenerated automatically after every publish and on the daily prune timer, built
by the stdlib-only `scripts/build_scratch_index.py` from the `_artifact.json` sidecar each
Python helper writes (falling back to the page `<title>` + mtime for artifacts published
with the raw shell command). Hand the operator the index URL when they want to *find* an
artifact rather than open a specific one; hand a direct `/scratch/<slug>/…` URL for a
specific report.

## HTML by default; render markdown only to *view* an existing markdown

Default to **rich HTML** for exhibits and reports — graphics, diagrams, and interactivity
convey concepts far better than plain prose, so HTML is the better choice for most
deliverables. The markdown path is for a narrower case: when the operator wants to **see an
existing markdown document displayed easily** (e.g. "show me this `.md`", "render this
doc"). Then publish it through `publish_markdown_to_scratch` (or pass markdown to the skill)
so it renders as a styled, light-mode page with a working TOC and the review toolbar —
rather than hand-converting it to bespoke HTML. Rule of thumb: *viewing a markdown → render
markdown; building an exhibit → author HTML.*

## Every publish is auto-archived (durable, dated, per-project)

After **every** successful publish, a permanent dated copy + an ongoing index are written
to a per-project archive (`scripts/scratch_archive.py`, wired into `publish_scratch.sh`),
so there's a standing record of every exhibit — independent of the docs system and **never
pruned**. You do nothing extra; publish as usual.

- **Interactive runs (desktop):** archived into a git-tracked `<repo>/scratch_archive/`
  *inside whatever repo you're working in* — per-project.
- **Autonomous runs (VPS):** archived into `/home/ua/ua_scratch_archive/`, served read-only
  at `https://uaonvps.taildcc090.ts.net/scratch-archive/`.
- Each archive root holds `INDEX.md` (newest-first, open this), `index.html` (searchable),
  `index.jsonl` (ledger), and dated `<YYYY-MM-DD>/<HHMMSS>__<slug>__<name>` copies.
- Knobs: `UA_SCRATCH_ARCHIVE_ENABLED=0` disables it; `UA_SCRATCH_ARCHIVE_ROOT` overrides the
  root. Best-effort — archiving never fails a publish.

> **MANDATORY final step for interactive (desktop) publishes — commit the archive entry.**
> The archiver *writes* the durable copy + index but does **not** commit, so the artifact is
> not "saved in the project" until you land it. After publishing, `git add scratch_archive/`
> and ship it (branch → PR → auto-merge). `scratch_archive/**` is `paths-ignore`d in
> `deploy.yml`, so committing it never restarts prod. This is the step most often skipped —
> `publish_scratch.sh` now prints a stderr reminder when `scratch_archive/` is left
> uncommitted; treat it as a required to-do, not a warning to dismiss. Commit **only your own**
> new entry — don't sweep in another session's untracked artifacts (no cross-session commits).

## Two-way review (mark up → respond) — you ↔ Claude Code

**Every** scratchpad HTML page carries a **review toolbar** — rendered markdown/doc-set pages
(via `scratch_publish.py::_html_page`) *and* hand-authored HTML exhibits alike (injected by
`scratch_publish.py::_inject_review_toolbar`, so your visual-explainer pages are markable too).
The operator can **highlight text → + Comment** (anchored to a stable selector + occurrence
index, so repeated text resolves to the right spot), **+ Element** to tap a diagram / image /
table / chart, or **+ Note** for a general remark, then clicks **Submit**, which (1) downloads
the comments to `~/Downloads/scratch-comments-<slug>.json` and (2) copies a ready-to-paste
prompt to the clipboard. An **open-time layout audit** flags overflow / clipped / unrendered-
Mermaid defects in a dismissible banner and records them in `layout_audit[]` in that JSON.
There is **no backend and no project routing** — the responder is *this Claude Code assistant*,
not the task hub / VP / project Telegram.

**When you (Claude Code) see a pasted message starting with `[scratch-review <slug>]`:**
read the operator's comments and respond, continuing the conversation. The paste itself is
self-contained (it inlines the comments with their `§ heading` / `▣ element` location and the
`@ <selector> [occ N]` anchor), but for full fidelity read
`~/Downloads/scratch-comments-<slug>.json` — each comment carries `target` / `selector` / `nth`
/ `elementLabel`, and `layout_audit[]` lists any render defects the page detected — then re-read
the artifact and address each comment.

**Refine in place:** publish iterative exhibits with a stable `artifact_id=` so re-publishing
**overwrites the same URL** (one living exhibit you keep refining) instead of minting a new
random slug — `publish_markdown_to_scratch(md, artifact_id="qloop-handoff")`. One-off reports
omit it and get the random unguessable slug as before.

## Generating the HTML itself

This skill publishes HTML — it doesn't author it. To produce a polished page, use the
**visual-explainer** skill (architecture overviews, diffs, plan recaps, comparison
tables) or render your own markdown→HTML. Whatever produces the HTML, this skill is the
last step: publish it and hand over the link.

## Mermaid that renders (avoid these syntax errors)

Mermaid diagrams fail **silently** in these artifacts: a syntax error shows only as a red
"Syntax error in text" box in the browser — it does **not** fail CI, the publish, or any
lint, so you won't notice unless you look. Broken diagrams are the single most common way
an otherwise-good page ships flawed. Follow these rules (mermaid 10.x), then **re-open the
published page — or have the operator confirm — to verify every diagram actually rendered.**

1. **No `;` inside a node / edge / message label.** Mermaid treats `;` as a statement
   separator, so a `;` in label text splits the line mid-text and throws. This is the exact
   bug that broke a `sequenceDiagram` message `(AUTO_MERGE_PAT; excludes …)`. Use a comma
   or "—" instead. (A trailing `classDef …;` / `class …;` line is fine — there the `;` is a
   real terminator, not label text.)
2. **Quote every label that contains a special character.** Write node labels as `id["…"]`,
   decisions as `id{"…"}`, and edge labels as `-->|"…"|` whenever the text contains any of
   `( ) / : + # & > < , .` or `<br/>`. Unquoted special chars are the #1 parse failure.
3. **Never put a raw `&` in a label** — mermaid reads it as the start of an HTML entity.
   Write "and".
4. **Prefer `flowchart` / `stateDiagram-v2` / a plain HTML table over `sequenceDiagram`.**
   Sequence diagrams are the most fragile (participant aliases *and* message text both parse
   special chars) and break most often. If you must use one: no `;`, avoid `()` and `/` in
   message text, keep `as` aliases simple, and use "and" not `&`.
5. **Keep node ids alphanumeric** (no spaces / dots / slashes in the id itself — put those
   in the quoted label), and **balance everything**: every `subgraph` has an `end`; brackets,
   quotes, and `|…|` pairs all matched.
6. **Validate before you hand over the link.** If `mmdc` (`@mermaid-js/mermaid-cli`) is on
   PATH, render each diagram with it; otherwise eyeball every mermaid block against rules
   1–5. The fix loop is cheap: edit the source, re-publish with the **same slug** (it
   overwrites the same URL), and reload the page.

## Light mode is mandatory

Operator reports must render in **light mode**, regardless of the device's dark-mode
setting. Kevin reads these on a phone that's often in dark mode, and a report that
auto-inverts to dark (or, worse, half-inverts) is hard to read and looks broken. The
generator's default theme does NOT count — many HTML generators (incl. some
visual-explainer outputs) default to dark or follow `prefers-color-scheme`. You are
responsible for forcing light at publish time.

Before publishing, confirm the HTML's `<head>` pins light mode and the body sets an
explicit light background + dark text — never rely on user-agent defaults:

```html
<head>
  <meta charset="utf-8">
  <meta name="color-scheme" content="light">   <!-- stops dark-mode UAs auto-inverting -->
  <style>
    :root { color-scheme: light; }
    body  { background: #ffffff; color: #1f2328; }   <!-- explicit, not UA default -->
  </style>
</head>
```

Also: do NOT ship a `@media (prefers-color-scheme: dark) { ... }` block that darkens the
page — that's the most common way a "light" report still renders dark on Kevin's phone.
If you used visual-explainer (or any generator), open the produced HTML and verify these
before calling `publish_scratch.sh`. The canonical reference implementation is the
YouTube digest report CSS (`youtube_daily_digest.py::_DIGEST_HTML_HEAD_CSS` +
`_render_full_digest_html`) — match its GitHub-light palette.

## Notes and boundaries

- **Tailnet-only / Private Preferred.** The scratchpad link is private to Kevin's devices and dead off-tailnet. It is the preferred default option for internal/private operator reports. Never use the scratchpad for artifacts going to external recipients.
- **Public Alternative (`here.now`):** If you need to share a static page publicly (outside the tailnet), use the `here-now` skill instead.
  - *Best for:* Agent-generated static artifacts that need to be publicly shareable — reports, landing pages, prototypes, portfolios, one-pagers, file sharing.
  - *Not for:* Anything we currently run on the VPS — the dashboard, the agent runtime, API endpoints, databases, WebSocket connections, authentication flows.
  - *Overlaps with:* The Tailscale scratchpad, but `here.now` is public while the scratchpad is tailnet-private. They serve different visibility needs.
- **Survives deploys.** `/home/ua/ua_scratch` lives in `ua`'s home, not under
  `/opt/universal_agent`, so published reports persist across deploys.
- **Don't disturb other serve mappings.** Only ever touch the `/scratch` path. The
  script's `--init`/`--status` are scoped to it; the `/`→dashboard and `:8443` mappings
  must stay intact. Verify with `--status` after any serve change.

## Canonical reference

`project_docs/06_platform/06_networking_tailscale_proxy_sshfs.md` § 1.6 documents the
mechanism, the serve topology, and failure signatures.
