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
  an HTML page (e.g. via the visual-explainer skill) and need to surface it. Do NOT use
  it for artifacts going to EXTERNAL recipients — the scratchpad is tailnet-only and the
  link is dead off-tailnet (attach a file or use a public host for those).
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
# Publish an HTML file. Prints ONLY the URL to stdout (status messages go to stderr),
# so you can capture it directly.
URL=$(scripts/publish_scratch.sh report.html)

# Optional readable slug names the subdir -> /scratch/<slug>/report.html
URL=$(scripts/publish_scratch.sh report.html my-analysis)

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
runs without an LLM in the loop. For those callers, use the thin helper that wraps the
**same** `publish_scratch.sh`, so there's one mechanism and no drift:

```python
from universal_agent.services.scratch_publish import publish_html_to_scratch

url = publish_html_to_scratch(
    html_str,                      # the rendered HTML as a string
    slug="yt-digest-2026-06-02",   # optional; a random suffix is appended for uniqueness
    filename="digest.html",        # optional; the file name within the slug dir
)
if url:
    # lead the email/notification with `url`
    ...
else:
    # publish failed — fall back to attaching the artifact so delivery is never dropped
    ...
```

`publish_html_to_scratch` returns the URL string on success or `None` on failure (it
never raises for an ordinary publish error), so callers can degrade gracefully — a
raw-but-delivered fallback always beats a dropped report. See
`src/universal_agent/services/scratch_publish.py`.

## Generating the HTML itself

This skill publishes HTML — it doesn't author it. To produce a polished page, use the
**visual-explainer** skill (architecture overviews, diffs, plan recaps, comparison
tables) or render your own markdown→HTML. Whatever produces the HTML, this skill is the
last step: publish it and hand over the link.

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
