# 060: Atomic Research Tasks - Architecture Evolution

**Date:** 2026-01-14  
**Status:** Design Observation  
**Related:** 057_RESEARCH_PIPELINE_EVALUATION.md, 058_RESEARCH_PIPELINE_ONE_SHOT_EVALUATION.md

---

## Executive Summary

The `research-specialist` sub-agent has been evolved from a "finalize only" helper into a **self-contained, full-service research unit**. This change makes research an **atomic, assignable, parallelizable task** - analogous to a coding feature in a development sprint.

---

## Architectural Change

### Before (Split Research)
```
Primary Agent
├── COMPOSIO Search (accumulates in context)
├── Delegate to research-specialist
│   └── finalize_research only
└── Delegate to report-writer
```

### After (Atomic Research)
```
Primary Agent
├── Delegate to research-specialist (IMMEDIATELY)
│   ├── COMPOSIO Search
│   ├── finalize_research (crawl → filter)
│   └── Return with complete corpus
└── Delegate to report-writer
```

---

## Key Properties of Atomic Research

| Property | Benefit |
|----------|---------|
| **Self-Contained** | No dependencies on primary agent context |
| **Fresh Context** | Sub-agent starts with maximum context window |
| **Discrete Artifact** | Produces `tasks/{topic}/research_overview.md` |
| **Verifiable** | Binary check: does overview exist with sources? |
| **Retryable** | Failed research can be re-run independently |
| **Parallelizable** | Multiple research tasks can run concurrently |

---

## Harness Task Decomposition Implications

### Research as a Task Type

In `mission.json`, research tasks can now be modeled like features:

```json
{
  "tasks": [
    {"id": "task_001", "type": "research", "topic": "military_operations"},
    {"id": "task_002", "type": "research", "topic": "diplomatic_efforts"},
    {"id": "task_003", "type": "research", "topic": "humanitarian_crisis"},
    {"id": "task_004", "type": "synthesis", "depends_on": ["task_001", "task_002", "task_003"]}
  ]
}
```

### Decomposition Strategies

**Horizontal (Parallel Research)**
- Multiple independent research topics
- Non-blocking until synthesis phase
- Example: Multi-aspect intelligence report

**Vertical (Serial Deep-Dive)**
- Single focused research topic
- Each phase builds on previous
- Example: Single-topic expert analysis

### Parallelization Considerations

| Can Parallelize | Blocking On |
|-----------------|-------------|
| Multiple `research` tasks | Nothing (independent) |
| `synthesis` task | All `research` dependencies |
| `delivery` task | `synthesis` completion |

---

## Future Design Considerations

1. **Parallel Research Execution**
   - Harness could spawn multiple research-specialist instances
   - Each produces independent corpus
   - Merge corpora for synthesis phase

2. **Research Caching**
   - Completed research persists in `tasks/{topic}/`
   - Can be reused across sessions
   - Enables "incremental research" updates

3. **Research Quality Gates**
   - Minimum source count threshold
   - Minimum word count per source
   - Source diversity requirements
   - **Refined Corpus Benchmark** (see below)

4. **Research Scope Estimation**
   - Predict search query count based on topic complexity
   - Adjust timeouts and budgets accordingly

---

## Refined Corpus Benchmark (Task Completion Check)

The `refined_corpus.md` file serves as the **primary benchmark artifact** for verifying that research has completed successfully. This enables a fast, deterministic check for research task completion.

### Why `refined_corpus.md`?

| Property | Benefit |
|----------|---------|
| **Single File** | One path to check vs. scanning `filtered_corpus/` |
| **Token-Efficient** | Pre-compressed (~5-11K tokens vs ~50K original) |
| **Citation-Rich** | Contains sources, dates, and attribution |
| **LLM-Ready** | Can be directly injected into report agent context |

### Automated Quality Check

A fast model (e.g., `glm-4.7`) can evaluate the refined corpus in ~2 seconds:

```python
async def check_refined_corpus_quality(task_dir: Path) -> dict:
    """
    Quick LLM judge to verify research corpus quality.
    Returns pass/fail with reason.
    """
    refined_path = task_dir / "refined_corpus.md"
    
    # 1. File existence check
    if not refined_path.exists():
        return {"passed": False, "reason": "refined_corpus.md not found"}
    
    content = refined_path.read_text()
    
    # 2. Basic sanity checks (no LLM needed)
    word_count = len(content.split())
    if word_count < 500:
        return {"passed": False, "reason": f"Too short ({word_count} words)"}
    
    # 3. Fast LLM judge (optional, ~2s)
    prompt = f"""
    Rate this research corpus 1-5 for completeness:
    - 1: Empty/corrupt
    - 3: Partial (missing sources or thin content)
    - 5: Complete (multiple sources, citations, substantive facts)
    
    First line: Just the number (1-5)
    Second line: Brief reason
    
    Corpus preview (first 2000 chars):
    {content[:2000]}
    """
    # ... fast model call ...
    
    return {"passed": score >= 3, "score": score, "reason": reason}
```

### Harness Integration

In `mission.json`, research task completion can now use this benchmark:

```json
{
  "tasks": [
    {
      "id": "task_001",
      "type": "research",
      "topic": "military_operations",
      "completion_check": {
        "type": "refined_corpus_benchmark",
        "min_word_count": 500,
        "min_score": 3
      }
    }
  ]
}
```

### Check Hierarchy

1. **File Exists** → `tasks/{topic}/refined_corpus.md`
2. **Minimum Size** → Word count > 500
3. **LLM Quality Score** (optional) → Score ≥ 3/5
4. **Pass** → Research task marked complete

---

## Files Modified in This Change

| File | Change |
|------|--------|
| `.claude/agents/research-specialist.md` | Full 3-step workflow |
| `agent_core.py` | Updated prompt builder |
| `main.py` | Added Composio tools to agent definition |
| `main.py` | Updated delegation instructions |
| `.claude/knowledge/report_workflow.md` | Updated workflow docs |

---

## Conclusion

Research is now a first-class, atomic task unit in the harness architecture. This enables more flexible mission decomposition, better parallelization, and cleaner separation of concerns between the coordinator (primary agent) and specialists (sub-agents).
