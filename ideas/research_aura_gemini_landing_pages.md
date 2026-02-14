# Research: Aura (Gemini) Landing Page Design Program

We should research **Aura**, the design program from **Gemini**, specifically for generating and iterating on **landing pages**.

## What To Confirm
- What Aura is (product scope, where it lives, availability, pricing/limits).
- Supported workflows:
  - Generate a landing page from prompt/brief
  - Edit/iterate (layout, copy, branding)
  - Component/library reuse
  - Export options (HTML/CSS/React, assets, Figma, etc.)
- Guardrails/constraints:
  - Brand kit inputs (logo/colors/fonts)
  - Accessibility (contrast, semantic HTML)
  - Responsiveness (mobile-first, breakpoints)
- Integration fit for our stack:
  - Can we automate it (API, CLI, SDK)?
  - If no API: best human-in-the-loop workflow and artifact handoff
- Quality bar:
  - Does it produce non-generic layouts?
  - Can it match an existing design system?

## Proposed Evaluation
- Pick 3 sample briefs (SaaS, local services, content site).
- Produce 2 iterations each (baseline + revision pass).
- Score outputs on:
  - Visual distinctiveness
  - Conversion clarity (CTA, hierarchy)
  - Mobile layout quality
  - Performance and semantic correctness (if exportable)

## Next Step
- Find official docs / announcement and collect concrete capabilities + export formats, then decide whether we should add an Aura-related skill/workflow into Universal Agent.

