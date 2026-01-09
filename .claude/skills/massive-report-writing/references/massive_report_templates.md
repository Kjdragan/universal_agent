# Massive Report Templates

## Evidence Ledger Template
Use one entry per source or tightly related cluster.

```
- id: EVID-001
  title: "NVIDIA Releases New Physical AI Models"
  url: https://nvidianews.nvidia.com/...
  date: 2026-01-05
  publisher: NVIDIA
  key_facts:
    - "Announced new physical AI models for robotics."
    - "Partners include Boston Dynamics and Caterpillar."
  numbers:
    - "No specific metrics provided"  # Use real numbers when available
  quotes:
    - "..."
  raw_excerpt:
    - "Verbatim sentence or short paragraph from the source."
  tags: [hardware, robotics, partnerships]
  confidence: high
```

## Batch Summary Template
Keep each batch summary under ~500-800 words.

```
Batch Summary (files 1-5):
- Theme highlights (3-5 bullets max, include evidence IDs)
- Notable numbers/dates with evidence IDs
- Contradictions or gaps with evidence IDs
- Key candidates for executive summary with evidence IDs

Note: summaries are navigation only. Final writing must use ledger items.
```

## Section Outline Template
Aim for 4-8 sections plus executive summary and references.

```
1. Executive Summary
2. Funding & Investment Landscape (EVID-001, EVID-005, EVID-014)
3. Model Releases & Research Breakthroughs (EVID-002, EVID-008)
4. Products, Platforms, and Tooling (EVID-004, EVID-010)
5. Infrastructure & Hardware (EVID-006, EVID-011)
6. Policy, Safety, and Regulation (EVID-003, EVID-009)
7. Forward Outlook & Open Questions
8. References
```

## Chunked Write Sequence (HTML or Markdown)
Use when single-shot Write calls fail or context is tight.

```
1) Write header + CSS + title block
2) Append executive summary
3) Append Section 1
4) Append Section 2
... repeat ...
N) Append references
```

## Compaction Handoff Prompt (If Needed)
Use to request a fresh context before final write.

```
Please compact prior context and keep only:
- Final section outline
- Evidence ledger (condensed)
- Output format requirements
- Target file path
Then re-invoke the report writer with those artifacts only.
```
