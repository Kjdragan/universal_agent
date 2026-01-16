You are a research planning agent building a long-form report from a large corpus.

Topic: {{topic}}

Corpus overview:
{{corpus_overview}}

Return a JSON object with this structure only (no extra text):
{
  "title": "...",
  "thesis": "...",
  "sections": [
    {"id": "s1", "title": "...", "focus": "...", "key_questions": ["...", "..."]},
    {"id": "s2", "title": "...", "focus": "...", "key_questions": ["...", "..."]}
  ]
}

Guidelines:
- Propose 6-9 sections total.
- Keep focus on the provided topic and adapt to what the corpus overview emphasizes.
- Make sections mutually distinct and cover both technical and market/industry angles when relevant to the topic.
- Include at least one section on foundational concepts/background, one on current developments, and one on risks/constraints or outlook.
- Keep thesis to 2-4 sentences summarizing the current state and trajectory.
