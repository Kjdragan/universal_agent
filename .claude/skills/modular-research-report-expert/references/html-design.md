# HTML Design System — PDF-First Reports

A modern design system for research reports optimized for **PDF as the primary
output format**. The HTML is an intermediate artifact; the reader experiences
the report as a printed/exported PDF document.

---

## Design Philosophy

- **PDF-first.** Every design decision is validated against Chrome `--print-to-pdf`.
  If it doesn't render well in PDF, it doesn't ship. HTML viewing is a bonus.
- **Magazine, not academic paper.** Think long-form journalism (The Atlantic, Ars Technica)
  not LaTeX. Wide images, pull-quotes, breathing room.
- **Content-first typography.** The text is the product. Typography choices serve
  readability above all.
- **Purposeful visuals.** Every image, diagram, and stat-card earns its space by
  conveying information the text alone cannot.
- **No interactive effects by default.** Hover states, transitions, and animated
  elements are gated behind `ENABLE_INTERACTIONS` (off by default). PDF readers
  don't have hover. Design for static consumption first.

---

## Parameter: `ENABLE_INTERACTIONS`

```
Default: off (0)
```

Set via environment variable:
```bash
export REPORT_ENABLE_INTERACTIONS=1
```

When enabled, the Storyteller adds `class="interactive"` to the `<html>` element
during assembly. This activates:
- Stat card hover lift effect
- Table row highlight on hover
- TOC link underline on hover
- General link hover underlines

When disabled (default), the report is purely static — identical experience
whether viewed in a browser or printed to PDF.

---

## CSS Variables (Design Tokens)

```css
:root {
  /* Typography */
  --font-heading: 'Segoe UI', system-ui, -apple-system, sans-serif;
  --font-body: 'Segoe UI', system-ui, -apple-system, sans-serif;
  --font-mono: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  --font-accent: Georgia, 'Times New Roman', serif;  /* For pull-quotes */
  --line-height: 1.75;
  --line-height-heading: 1.2;

  /* Color Palette — Deep Blue Professional */
  --color-primary: #1a365d;
  --color-primary-light: #2c5282;
  --color-accent: #2b6cb0;
  --color-accent-light: #ebf8ff;
  --color-accent-vivid: #3182ce;
  --color-success: #276749;
  --color-success-light: #f0fff4;
  --color-warning: #975a16;
  --color-warning-light: #fffff0;
  --color-danger: #9b2c2c;
  --color-danger-light: #fff5f5;
  --color-text: #1a202c;
  --color-text-secondary: #4a5568;
  --color-text-muted: #718096;
  --color-border: #e2e8f0;
  --color-border-light: #edf2f7;
  --color-bg: #ffffff;
  --color-bg-alt: #f7fafc;
  --color-bg-warm: #fffaf0;
  --color-bg-dark: #1a202c;

  /* Spacing Scale */
  --space-xs: 0.25rem;
  --space-sm: 0.5rem;
  --space-md: 1rem;
  --space-lg: 1.5rem;
  --space-xl: 2rem;
  --space-2xl: 3rem;
  --space-3xl: 4rem;
  --space-4xl: 6rem;

  /* Shadows */
  --shadow-sm: 0 1px 3px rgba(0,0,0,0.08);
  --shadow-md: 0 4px 12px rgba(0,0,0,0.1);
  --shadow-lg: 0 8px 24px rgba(0,0,0,0.12);
  --shadow-inset: inset 0 2px 4px rgba(0,0,0,0.06);

  /* Border Radius */
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
}
```

---

## Layout

```css
body {
  max-width: 52rem;  /* ~832px — optimal reading width */
  margin: 0 auto;
  padding: 0 var(--space-xl) var(--space-4xl);
  font-family: var(--font-body);
  color: var(--color-text);
  line-height: var(--line-height);
  background: var(--color-bg);
  -webkit-font-smoothing: antialiased;
}
```

---

## Component Library

### 1. Hero Header

Full-bleed header with gradient background and optional background image.

```css
.report-hero {
  position: relative;
  margin: 0 -2rem var(--space-3xl);
  padding: var(--space-4xl) var(--space-2xl);
  background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-accent) 50%, var(--color-primary-light) 100%);
  color: white;
  text-align: center;
  overflow: hidden;
  min-height: 320px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
}
.report-hero .hero-bg {
  position: absolute;
  inset: 0;
  width: 100%; height: 100%;
  object-fit: cover;
  opacity: 0.15;
  mix-blend-mode: luminosity;
}
.report-hero h1 {
  position: relative;
  font-size: clamp(2rem, 5vw, 3rem);
  font-weight: 800;
  line-height: var(--line-height-heading);
  margin-bottom: var(--space-md);
  letter-spacing: -0.02em;
  max-width: 800px;
}
.report-hero .subtitle {
  position: relative;
  font-size: clamp(1rem, 2.5vw, 1.3rem);
  opacity: 0.9;
  max-width: 640px;
  line-height: 1.5;
  font-weight: 400;
}
.report-hero .report-date {
  position: relative;
  margin-top: var(--space-lg);
  font-size: 0.9rem;
  opacity: 0.65;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
```

### 2. Table of Contents

```css
.toc {
  background: var(--color-bg-alt);
  border-radius: var(--radius-lg);
  padding: var(--space-xl) var(--space-2xl);
  margin: var(--space-xl) 0 var(--space-2xl);
  border: 1px solid var(--color-border-light);
}
.toc h2 {
  font-size: 1.1rem;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: var(--space-lg);
  border: none;
  padding: 0;
  font-weight: 600;
}
.toc ol { padding-left: var(--space-lg); counter-reset: toc-counter; }
.toc li {
  margin-bottom: var(--space-sm);
  line-height: 1.5;
}
.toc a {
  color: var(--color-accent);
  text-decoration: none;
  font-weight: 500;
  transition: color 0.15s;
}
/* .toc a:hover — gated behind html.interactive */
```

### 3. Section Headers

```css
section {
  margin-bottom: var(--space-3xl);
  scroll-margin-top: var(--space-xl);
}
section h2 {
  font-size: 1.75rem;
  font-weight: 700;
  color: var(--color-primary);
  border-bottom: 3px solid var(--color-accent);
  padding-bottom: var(--space-sm);
  margin-bottom: var(--space-lg);
  line-height: var(--line-height-heading);
}
section h3 {
  font-size: 1.3rem;
  font-weight: 600;
  color: var(--color-primary-light);
  margin-top: var(--space-2xl);
  margin-bottom: var(--space-md);
}
section h4 {
  font-size: 1.05rem;
  font-weight: 600;
  color: var(--color-text);
  margin-top: var(--space-lg);
  margin-bottom: var(--space-sm);
}
```

### 4. Key Finding Card

Highlight box for the single most important takeaway per section.

```css
.key-finding {
  background: var(--color-accent-light);
  border-left: 4px solid var(--color-accent);
  padding: var(--space-lg) var(--space-xl);
  margin: var(--space-xl) 0;
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
  font-size: 1.02rem;
}
.key-finding strong {
  color: var(--color-primary);
  font-weight: 700;
}
.key-finding p { margin-bottom: var(--space-sm); }
.key-finding p:last-child { margin-bottom: 0; }
```

### 5. Stat Cards

Row of highlighted numbers. Maximum 4 per row for readability.

```css
.stats-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-md);
  justify-content: center;
  margin: var(--space-2xl) 0;
}
.stat-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  background: white;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-lg) var(--space-xl);
  box-shadow: var(--shadow-sm);
  min-width: 150px;
  flex: 1;
  max-width: 220px;
}
/* Hover only when html.interactive: see Interactive Effects section below */
.stat-number {
  font-size: 2.25rem;
  font-weight: 800;
  color: var(--color-accent-vivid);
  line-height: 1;
  letter-spacing: -0.02em;
}
.stat-label {
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin-top: var(--space-sm);
  text-align: center;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-weight: 500;
}
```

### 6. Callout Boxes

```css
.callout {
  background: var(--color-bg-alt);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-lg) var(--space-xl);
  margin: var(--space-lg) 0;
  font-size: 0.95rem;
}
.callout strong { display: block; margin-bottom: var(--space-xs); }
.callout.warning {
  background: var(--color-warning-light);
  border-color: var(--color-warning);
}
.callout.success {
  background: var(--color-success-light);
  border-color: var(--color-success);
}
.callout.danger {
  background: var(--color-danger-light);
  border-color: var(--color-danger);
}
```

### 7. Blockquotes & Pull Quotes

Standard blockquote for inline quotes. Pull-quote for featured statements.

```css
blockquote {
  border-left: 4px solid var(--color-accent);
  margin: var(--space-lg) 0;
  padding: var(--space-md) var(--space-lg);
  background: var(--color-bg-alt);
  font-style: italic;
  color: var(--color-text-secondary);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
blockquote cite {
  display: block;
  margin-top: var(--space-sm);
  font-size: 0.85rem;
  font-style: normal;
  font-weight: 600;
  color: var(--color-text);
}

/* Pull Quote — large, centered, magazine-style */
.pull-quote {
  margin: var(--space-2xl) var(--space-xl);
  padding: var(--space-xl) 0;
  border-top: 2px solid var(--color-accent);
  border-bottom: 2px solid var(--color-accent);
  text-align: center;
}
.pull-quote p {
  font-family: var(--font-accent);
  font-size: 1.4rem;
  line-height: 1.5;
  color: var(--color-primary);
  font-style: italic;
  margin-bottom: var(--space-sm);
}
.pull-quote cite {
  font-family: var(--font-body);
  font-size: 0.85rem;
  font-style: normal;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

### 8. Images & Figures

```css
.report-image {
  margin: var(--space-2xl) 0;
  text-align: center;
}
.report-image img {
  max-width: 100%;
  height: auto;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
}
.report-image figcaption {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  margin-top: var(--space-md);
  font-style: italic;
  line-height: 1.4;
}

/* Full-bleed image — extends to body padding edges (PDF-safe) */
.report-image.full-bleed {
  margin-left: -2rem;
  margin-right: -2rem;
}
.report-image.full-bleed img {
  width: 100%;
  border-radius: 0;
}

/* Side-by-side images */
.image-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-md);
  margin: var(--space-2xl) 0;
}
.image-pair .report-image { margin: 0; }
```

### 9. Diagram Containers

```css
.diagram-container {
  margin: var(--space-2xl) 0;
  text-align: center;
  background: white;
  padding: var(--space-xl);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
  box-shadow: var(--shadow-sm);
}
.diagram-container img { max-width: 100%; height: auto; }
.diagram-container figcaption {
  font-size: 0.85rem;
  color: var(--color-text-muted);
  margin-top: var(--space-md);
}
```

### 10. Tables

```css
.table-wrapper {
  overflow-x: auto;
  margin: var(--space-lg) 0;
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.92rem;
}
thead {
  background: var(--color-primary);
  color: white;
}
th {
  padding: var(--space-md) var(--space-lg);
  text-align: left;
  font-weight: 600;
  letter-spacing: 0.02em;
}
td {
  padding: var(--space-md) var(--space-lg);
  border-bottom: 1px solid var(--color-border-light);
}
tbody tr:nth-child(even) { background: var(--color-bg-alt); }
/* tbody tr:hover — gated behind html.interactive */
```

### 11. Section Divider

Visual separator between major report sections.

```css
.section-divider {
  margin: var(--space-3xl) auto;
  width: 60px;
  height: 3px;
  background: var(--color-accent);
  border: none;
  border-radius: 2px;
}
```

### 12. Footer

```css
.report-footer {
  margin-top: var(--space-4xl);
  padding: var(--space-xl) 0;
  border-top: 2px solid var(--color-border);
  font-size: 0.85rem;
  color: var(--color-text-muted);
  text-align: center;
  line-height: 1.6;
}
.report-footer a { color: var(--color-accent); text-decoration: none; }
```

### 13. Placeholder Slots (Removed During Assembly)

```css
.image-slot, .diagram-slot {
  /* Invisible in final output — only used during drafting */
  display: none;
}
```

---

## Interactive Effects (Gated — Off by Default)

All hover/transition effects are gated behind `html.interactive`. The Storyteller
adds this class during assembly only when `REPORT_ENABLE_INTERACTIONS=1`.

```css
/* Only active when html element has class="interactive" */
html.interactive .stat-card {
  transition: box-shadow 0.15s, transform 0.15s;
}
html.interactive .stat-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
}
html.interactive .toc a:hover {
  color: var(--color-primary);
  text-decoration: underline;
}
html.interactive tbody tr:hover {
  background: var(--color-accent-light);
}
```

---

## Print / PDF Styles (Primary Output)

The PDF is the deliverable. These styles are not an afterthought — they are the
primary design target. Test every layout change against Chrome `--print-to-pdf`.

```css
@media print {
  :root {
    --color-bg: white;
    --color-bg-alt: #f8f9fa;
  }
  body {
    max-width: none;
    padding: 0 1cm;
    font-size: 11pt;
    line-height: 1.6;
  }
  /* Hero: cover page effect */
  .report-hero {
    break-after: page;
    margin: 0;
    min-height: auto;
    padding: 4cm 2cm 3cm;
  }
  .report-hero .hero-bg { display: none; }
  .report-hero h1 { font-size: 2.2rem; }
  /* Sections: avoid orphaned headings */
  section { break-inside: avoid; }
  section h2 { break-after: avoid; }
  section h3 { break-after: avoid; }
  /* Components: never split across pages */
  .key-finding, .callout, .report-image,
  .diagram-container, .pull-quote, blockquote { break-inside: avoid; }
  .stats-row { break-inside: avoid; }
  .stat-card { box-shadow: none; border: 1px solid #ccc; }
  /* Images: respect page bounds */
  .report-image img {
    max-height: 45vh;
    box-shadow: none;
    border: 1px solid #e0e0e0;
  }
  .report-image.full-bleed { margin-left: 0; margin-right: 0; }
  /* Tables: repeat headers */
  thead { display: table-header-group; }
  /* Links: plain text in print */
  a { color: var(--color-text); text-decoration: none; }
  /* TOC: own page */
  .toc { break-after: page; }
  /* Kill all interactive effects */
  html.interactive .stat-card:hover,
  html.interactive tbody tr:hover { all: unset; }
  @page { margin: 1.5cm; size: A4; }
  @page :first { margin-top: 0; }
}
```

---

## Responsive Behavior (Secondary — Optional HTML Viewing)

```css
@media (max-width: 768px) {
  body { padding: 0 1rem; }
  .report-hero { padding: 3rem 1rem; margin: 0 -1rem 2rem; }
  .stats-row { flex-direction: column; align-items: center; }
  .stat-card { max-width: 100%; min-width: auto; }
  .image-pair { grid-template-columns: 1fr; }
  .pull-quote { margin: 1.5rem 0; }
}
```
