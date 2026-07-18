# Operator reports → tailnet scratchpad link, not pasted markdown or attachments (2026-06-02)

> Moved verbatim from `memory/HEARTBEAT.md` (R4 context diet, 2026-07-18). Read this
> whenever you're about to produce an operator-facing report, analysis, digest, diff
> review, architecture diagram, or any rich artifact by email.

Kevin runs Claude Code **terminal-only** and reads mail in clients that strip hyperlinks and flatten HTML/PDF attachments. So whenever you produce an operator-facing **report, analysis, digest, diff review, architecture diagram, or any artifact with rich content** (styling, diagrams, or — critically — a table of contents whose entries should *jump* to a section), do NOT paste raw markdown into the email or attach a PDF/`.html` file. Those lose the rendering and the in-document navigation.

Instead: render the artifact as **standalone HTML**, publish it to the tailnet HTML scratchpad, and **lead the email with the link**. Invoke the **`publish-to-scratchpad` skill** (it wraps `scripts/publish_scratch.sh`; on the VPS it writes directly and prints the private `https://uaonvps.taildcc090.ts.net/scratch/...` URL). The page then opens fully rendered, with working anchors, on every one of Kevin's tailnet devices.

- The short email body still follows the **light-theme palette rules** in `memory/reference/daily-briefing-email-rendering.md` — only the heavy report moves to the scratchpad link.
- **The scratchpad report itself must render in light mode**, not just the email. Kevin reads on a dark-mode phone; pin the report's `<head>` to light (`<meta name="color-scheme" content="light">` + `:root{color-scheme:light}` + explicit `background:#ffffff; color:#1f2328`) and ship no `prefers-color-scheme: dark` override. The `publish-to-scratchpad` skill has the exact recipe; verify the generated HTML before publishing.
- The scratchpad is **tailnet-only** (private to Kevin's devices). Never use it for mail to external recipients — attach a file for those.
- The YouTube daily digest already does this in code (`youtube_daily_digest.py` → `scratch_publish.py::publish_html_to_scratch`, link-first with a PDF fallback). Mirror that pattern for anything you deliver by hand.
