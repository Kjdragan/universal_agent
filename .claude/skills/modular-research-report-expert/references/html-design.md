# HTML Design System for Research Reports

CSS design system and component patterns for the report HTML template.

---

## Typography

```css
:root {
  --font-heading: 'Segoe UI', system-ui, -apple-system, sans-serif;
  --font-body: 'Segoe UI', system-ui, -apple-system, sans-serif;
  --font-mono: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  --line-height: 1.7;
  --line-height-heading: 1.2;
}
```

## Color Palette

```css
:root {
  --color-primary: #1a365d;
  --color-primary-light: #2c5282;
  --color-accent: #2b6cb0;
  --color-accent-light: #ebf4ff;
  --color-success: #276749;
  --color-success-light: #f0fff4;
  --color-warning: #975a16;
  --color-warning-light: #fefcbf;
  --color-text: #1a202c;
  --color-text-secondary: #4a5568;
  --color-border: #e2e8f0;
  --color-bg: #ffffff;
  --color-bg-alt: #f7fafc;
  --color-bg-dark: #1a202c;
}
```

## Layout

```css
body {
  max-width: 900px;
  margin: 0 auto;
  padding: 0 2rem;
  font-family: var(--font-body);
  color: var(--color-text);
  line-height: var(--line-height);
}
```

## Component Classes

### Hero Header
```css
.report-hero {
  position: relative;
  margin: -2rem -2rem 3rem;
  padding: 4rem 3rem;
  background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-accent) 100%);
  color: white;
  text-align: center;
  overflow: hidden;
}
.report-hero img {
  position: absolute;
  top: 0; left: 0;
  width: 100%; height: 100%;
  object-fit: cover;
  opacity: 0.2;
}
.report-hero h1 {
  position: relative;
  font-size: 2.5rem;
  font-weight: 700;
  margin-bottom: 0.5rem;
}
.report-hero .subtitle {
  position: relative;
  font-size: 1.25rem;
  opacity: 0.9;
}
.report-hero .report-date {
  position: relative;
  margin-top: 1rem;
  font-size: 0.9rem;
  opacity: 0.7;
}
```

### Section Headers
```css
section { margin-bottom: 3rem; }
section h2 {
  font-size: 1.75rem;
  color: var(--color-primary);
  border-bottom: 3px solid var(--color-accent);
  padding-bottom: 0.5rem;
  margin-bottom: 1.5rem;
}
section h3 {
  font-size: 1.3rem;
  color: var(--color-primary-light);
  margin-top: 2rem;
}
```

### Key Finding Card
```css
.key-finding {
  background: var(--color-accent-light);
  border-left: 4px solid var(--color-accent);
  padding: 1.25rem 1.5rem;
  margin: 1.5rem 0;
  border-radius: 0 8px 8px 0;
}
.key-finding strong { color: var(--color-primary); }
```

### Stat Card
```css
.stat-card {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  background: white;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 1.5rem 2rem;
  margin: 0.5rem;
  box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  min-width: 160px;
}
.stat-number {
  font-size: 2.25rem;
  font-weight: 700;
  color: var(--color-accent);
  line-height: 1;
}
.stat-label {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  margin-top: 0.5rem;
  text-align: center;
}
.stats-row {
  display: flex;
  flex-wrap: wrap;
  gap: 1rem;
  justify-content: center;
  margin: 2rem 0;
}
```

### Callout Box
```css
.callout {
  background: var(--color-bg-alt);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 1.25rem 1.5rem;
  margin: 1.5rem 0;
}
.callout.warning {
  background: var(--color-warning-light);
  border-color: var(--color-warning);
}
.callout.success {
  background: var(--color-success-light);
  border-color: var(--color-success);
}
```

### Image & Diagram Containers
```css
.report-image {
  margin: 2rem 0;
  text-align: center;
}
.report-image img {
  max-width: 100%;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}
.report-image figcaption {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  margin-top: 0.75rem;
  font-style: italic;
}
.diagram-container {
  margin: 2rem 0;
  text-align: center;
  background: white;
  padding: 1.5rem;
  border-radius: 8px;
  border: 1px solid var(--color-border);
}
.diagram-container img { max-width: 100%; }
```

### Table of Contents
```css
.toc {
  background: var(--color-bg-alt);
  border-radius: 8px;
  padding: 1.5rem 2rem;
  margin: 2rem 0;
}
.toc h2 { border-bottom: none; font-size: 1.25rem; margin-bottom: 1rem; }
.toc ol { padding-left: 1.5rem; }
.toc li { margin-bottom: 0.4rem; }
.toc a {
  color: var(--color-accent);
  text-decoration: none;
}
.toc a:hover { text-decoration: underline; }
```

### Blockquote
```css
blockquote {
  border-left: 4px solid var(--color-accent);
  margin: 1.5rem 0;
  padding: 0.75rem 1.25rem;
  background: var(--color-bg-alt);
  font-style: italic;
  color: var(--color-text-secondary);
}
```

### Footer
```css
.report-footer {
  margin-top: 4rem;
  padding: 2rem 0;
  border-top: 2px solid var(--color-border);
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  text-align: center;
}
```

## Print / PDF Styles

```css
@media print {
  body { max-width: none; padding: 0; }
  .report-hero { break-after: page; }
  section { break-inside: avoid; }
  .key-finding, .stat-card, .callout { break-inside: avoid; }
  .report-image { break-inside: avoid; }
  a { color: var(--color-text); text-decoration: none; }
  @page { margin: 1.5cm; }
}
```
