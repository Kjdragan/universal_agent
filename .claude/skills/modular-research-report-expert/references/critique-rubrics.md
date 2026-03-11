# Critique Rubrics — Editorial Judge Reference

The Editorial Judge uses these rubrics to evaluate both written content and visual
assets. The Director provides this file to the Judge at spawn time.

---

## Part 1: Text Critique Rubric

### Dimension 1: Writing Quality & Voice (Weight: HIGH)

The baseline is **university-level writing**: clear, substantive, well-structured prose
with logical flow and precise language. Beyond that, the voice should match the
material — analytical reports need authority, narrative pieces need warmth, technical
reviews need precision. The #1 anti-pattern is "robotic enumeration" — facts listed
sequentially without connective tissue.

| Score | Description |
|-------|-------------|
| 9-10 | University-level or above. Clear authorial voice appropriate to the material. Compelling hooks, natural rhythm, precise language. |
| 7-8 | Professional and readable. Voice matches the topic. Occasional flat passages but mostly engaging. |
| 5-6 | Functional but generic. Facts present but listed rather than integrated. Voice doesn't match material type. |
| 3-4 | Robotic enumeration. Bullet points converted to paragraphs. No narrative flow. Reads like a summary, not a report. |
| 1-2 | Incoherent or reads like raw data dump. |

**Red flags to catch:**
- Sections opening with "This section discusses..." or "In this section, we will..."
- Every paragraph starting with a statistic
- No use of quotes, anecdotes, or scene-setting (when source voices exist)
- Transitions that are just "Additionally," or "Furthermore,"
- Uniform paragraph structure throughout (all same length, same pattern)
- Tone mismatched to material (e.g., breathless magazine voice on a technical comparison)
- Oversimplified language that doesn't respect the reader's intelligence

### Dimension 2: Coherence & Structure (Weight: HIGH)

| Score | Description |
|-------|-------------|
| 9-10 | Crystal clear narrative arc. Each section builds on the previous. Reader feels momentum. |
| 7-8 | Logical structure with good transitions. Minor flow interruptions. |
| 5-6 | Sections make sense individually but feel disconnected. Weak transitions. |
| 3-4 | Confusing organization. Sections seem arbitrarily ordered. |
| 1-2 | No discernible structure. Topics jump randomly. |

**Check for:**
- Does the `narrative_role` from the outline show in the writing? (Setup sections establish context, climax sections deliver revelations)
- Do transitions between sections create a "pull-forward" effect?
- Are cross-references between sections natural (not "as mentioned in Section 2")?
- Does the executive summary accurately reflect the report's actual content?

### Dimension 3: Source Fidelity (Weight: HIGH)

The research corpus comes from credible searches. The Judge's job is NOT to
fact-check the corpus — it is to ensure the Storyteller faithfully represents
what the corpus says without distortion, fabrication, or hallucination.

| Score | Description |
|-------|-------------|
| 9-10 | All claims traceable to corpus. Statistics match source material. Quote attributions correct. |
| 7-8 | Mostly faithful. Minor imprecisions in non-critical details. |
| 5-6 | Some claims not found in corpus, or statistics slightly distorted. |
| 3-4 | Multiple fabricated details or hallucinated claims not in the corpus. |
| 1-2 | Largely disconnected from source material. |

**Check for:**
- Statistics in the report match what the corpus says (no invented numbers)
- Quote attributions are correct (right person, right organization)
- Conclusions follow from presented evidence — no unsupported leaps
- No hallucinated claims, companies, products, or people not in the corpus
- Flag fabricated content as `type: accuracy, severity: critical`

**Do NOT:**
- Second-guess whether the corpus itself is correct
- Try to independently verify claims against external knowledge
- Add disclaimers about the credibility of sourced claims

### Dimension 4: Source Integration (Weight: MEDIUM)

| Score | Description |
|-------|-------------|
| 9-10 | Quotes woven naturally into narrative. Attribution feels organic. Multiple voices represented. No inline citations cluttering the prose. |
| 7-8 | Good use of quotes. Mostly natural integration. |
| 5-6 | Quotes feel bolted on. Block-quotes used for everything. Inline citation markers disrupt reading flow. |
| 3-4 | No direct quotes, or quotes poorly attributed. |
| 1-2 | No evidence of source material in the writing. |

**Note:** The report uses end-of-report bibliography style. There should be NO
inline `(Source: ...)` markers, footnote numbers, or superscript references in
the body text. Flag any inline citations as `type: style, severity: major`.

### Dimension 5: Visual Integration Planning (Weight: MEDIUM)

| Score | Description |
|-------|-------------|
| 9-10 | Image/diagram slots placed at perfect narrative breakpoints. Each visual serves the story. |
| 7-8 | Good placement. Minor adjustments would improve flow. |
| 5-6 | Slots present but feel arbitrary. Some break the reading flow. |
| 3-4 | Slots clustered together or placed mid-argument. |
| 1-2 | No slots, or slots with no relationship to content. |

### Dimension 6: Redundancy Control (Weight: MEDIUM)

| Score | Description |
|-------|-------------|
| 9-10 | Zero redundancy. Each fact appears once, in its most relevant context. |
| 7-8 | Minimal repetition. Only intentional callbacks. |
| 5-6 | Same statistics appear in 2-3 sections. Some paragraph-level repetition. |
| 3-4 | Significant redundancy across sections. Feels padded. |
| 1-2 | Wholesale duplication of content between sections. |

---

## Part 2: Visual Critique Rubric

Inspired by the banana-squad generate-critique pattern. Each visual is evaluated
independently, then the set is evaluated for coherence.

### Per-Image Evaluation

#### Relevance (Weight: HIGH)
Does this image match the `visual_brief` from the outline?

| Score | Verdict |
|-------|---------|
| 9-10 | Perfect match. Exactly what was described. → `approve` |
| 7-8 | Close match. Minor deviations acceptable. → `approve` |
| 5-6 | Partial match. Missing key elements from brief. → `revise` with notes |
| 3-4 | Generic image. Could be about anything. → `regenerate` |
| 1-2 | Completely unrelated to the brief. → `regenerate` |

#### Quality (Weight: MEDIUM)
Professional appearance, composition, resolution.

| Score | Verdict |
|-------|---------|
| 9-10 | Publication quality. Clean composition, professional look. → `approve` |
| 7-8 | Good quality. Minor aesthetic issues. → `approve` |
| 5-6 | Acceptable but not impressive. → `revise` if other issues exist |
| 3-4 | Noticeable artifacts, poor composition. → `regenerate` |
| 1-2 | Unusable quality. → `regenerate` |

#### Text Legibility (Infographics Only) (Weight: CRITICAL for infographics)
Can all text, numbers, and labels in the image be read clearly?

| Result | Verdict |
|--------|---------|
| `pass` | All text readable at normal zoom. |
| `fail` | Any text blurry, overlapping, cut off, or wrong. → `revise` or `regenerate` |
| `na` | Not an infographic / no text expected. |

**For infographics, this is a HARD GATE.** An infographic with illegible text MUST
be revised regardless of other scores. Use `generate_image_with_review` with
specific notes about which text elements failed.

#### Brand Consistency (Weight: LOW per image, HIGH for set)
Does this image feel like it belongs in the same report as the others?

| Result | Action |
|--------|--------|
| `pass` | Consistent color palette, style, and mood with other images. |
| `fail` | Clashing style. Note what needs to change in `revision_notes`. |

### Set-Level Evaluation

After reviewing all images individually, assess the visual set:

1. **Variety**: Do images cover different types (hero, infographic, accent) or are
   they all the same style?
2. **Coverage**: Are there gaps — sections with visual_type in the outline but no
   image generated?
3. **Balance**: Is the report visually balanced or front-loaded/back-loaded with images?
4. **Color coherence**: Do all images share a compatible color palette?

Write set-level notes in `overall_visual_coherence` field.

### Diagram Evaluation

| Criterion | Pass | Fail |
|-----------|------|------|
| **Accuracy** | Data matches corpus. Labels correct. Relationships accurate. | Wrong data, missing nodes, incorrect arrows. |
| **Readability** | Labels readable. Flow clear. Not overcrowded. | Cramped, overlapping, or unclear flow. |
| **Aesthetic** | Clean styling. Consistent with report color palette. | Default unstyled or clashing colors. |

---

## Part 3: Revision Guidance

### For the Storyteller (Text Revision)

When the Judge flags issues, prioritize in this order:

1. **Critical source fidelity issues** — Fix immediately, align with corpus
2. **Major voice issues** — Rewrite sections flagged as "robotic enumeration"
3. **Cross-section redundancy** — Remove duplicate facts, keep in most relevant section
4. **Major coherence issues** — Strengthen transitions, reorder if needed
5. **Major visual placement** — Move image/diagram slots as suggested
6. **Minor issues** — Fix in place during revision pass

**Rewrite trigger**: A section needs rewriting if:
- It has 2+ critical issues, OR
- It has 1+ voice issues with severity major+, OR
- `rewrite_needed: true` is explicitly set

### For the Visual Director (Image Revision)

When regenerating, the revision prompt should:
1. Start with the ORIGINAL prompt
2. Add: "REVISION: The previous version had these issues: {revision_notes}"
3. Add specific corrections: "Make sure the text '47%' is clearly legible"
4. For infographics, explicitly list ALL data points again

**Maximum 2 revision cycles per image.** After that:
- If latest version scores 5+ on relevance → accept it
- If still below 5 → skip this image, remove slot from HTML

### For the Diagram Craftsman (Diagram Revision)

Diagram revisions are typically syntax/data fixes:
- Wrong data → update the `.mmd` source
- Readability → add spacing, simplify labels, split into 2 diagrams if overcrowded
- Re-render after every change

---

## Part 4: Final Polish Checklist (Phase 6b)

Quick-pass checklist for the assembled `report.html`:

- [ ] No remaining `image-slot` or `diagram-slot` placeholder divs
- [ ] All `<img>` tags have valid `src` paths (relative to report location)
- [ ] All `<img>` tags have meaningful `alt` attributes
- [ ] Table of contents links match actual section `id` attributes
- [ ] No "TODO", "[INSERT]", "[PENDING]" text anywhere
- [ ] Executive summary reflects the actual report content
- [ ] Footer has generation date and source attribution
- [ ] No orphan `<cite>` or `<blockquote>` elements without content
- [ ] Print CSS won't break pages in awkward places
- [ ] All stat-card numbers match the corpus values
- [ ] Sources section present at end of report with bibliography entries
- [ ] No inline citation markers (footnotes, superscripts, "(Source: ...)") in body text
- [ ] No `{{SOURCES_SECTION}}` placeholder remaining
