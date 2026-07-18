# Daily Briefing Email Rendering (2026-05-12)

> Moved verbatim from `memory/HEARTBEAT.md` (R4 context diet, 2026-07-18). Read this
> whenever you are composing the daily morning briefing HTML email.

When composing the daily morning briefing HTML email (sent via AgentMail with subject pattern `UA Daily Briefing — <date>`), follow these rules. They are mandatory — the 2026-05-12 briefing shipped with a hand-rolled GitHub-dark palette (`#0d1117` body bg, `#8b949e` muted text) and was unreadable in Gmail web light mode because Gmail strips `<body>` background-color, leaving near-white text floating on white.

**Critical layout rule.** NEVER rely on `<body>` for background. Always wrap all content in an outer `<div>` (or `<table>`) with the background applied directly to that element — Gmail respects div/table `background-color`, just not `<body>`:

```html
<div style="background:#ffffff;padding:24px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <div style="max-width:720px;margin:0 auto;color:#1f2328;line-height:1.6;">
    <!-- briefing content -->
  </div>
</div>
```

**Palette — light theme (GitHub light, WCAG-AA on white).** Use these exact hex values; do not pick alternates:

- Outer page bg: `#ffffff` · Card bg: `#f6f8fa` · Card border: `#d1d9e0`
- Body text: `#1f2328` · Muted/subtitle/footer text: `#59636e` · Accent (H1, links, pr-num): `#0969da`
- Code bg: `#eff1f3` · Code text: `#1f2328`
- Metric value default `#1f2328`; green `#1a7f37`; amber `#7d4e00`; red `#cf222e`.
- Badge pairs (bg / text), all AA-compliant:
  - green `#dafbe1` / `#1a7f37` — amber `#fff8c5` / `#7d4e00` — red `#ffebe9` / `#cf222e`
  - blue `#ddf4ff` / `#0969da` — purple `#f5e6ff` / `#8250df` — gray `#eff1f3` / `#59636e`
- Insight boxes: default bg `#ddf4ff` border `#0969da`; success bg `#dafbe1` border `#1a7f37`; warning bg `#fff8c5` border `#7d4e00`. Text always `#1f2328`.

**Rules of thumb.**
- Never use text color lighter than `#59636e` on white/`#f6f8fa` backgrounds — anything lighter is invisible in Gmail.
- `#8b949e` is BANNED for text on light backgrounds (the cause of 2026-05-12 invisibility).
- Card style: `background:#f6f8fa; border:1px solid #d1d9e0; border-radius:8px; padding:16px`.
- Badge style: `display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600`.
- Keep the same structural sections used today (Infrastructure Health tiles, Shipping table, VP Worker Activity, Task Hub Status, Insight boxes, Recommended Actions) — only the palette changes.

**Self-check before sending.** Eyeball the HTML before invoking `send_message`: is there ANY text styled with a hex value lighter than `#59636e`? If yes, fix it. Is the body background applied to `<body>` only? If yes, move it to an outer `<div>`. Do all badges have a darker text color than their background? If no, fix it.
