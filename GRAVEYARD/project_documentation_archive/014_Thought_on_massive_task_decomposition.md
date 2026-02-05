# Massive Task Decomposition: Horizontal vs. Vertical Strategies

## Executive Summary
This document explores the architectural choices for decomposing massive objectives into manageable phases for the Universal Agent. We compare the traditional **Horizontal (Layered)** approach against the proposed **Vertical (Functional)** approach, focusing on context management, reliability, and the requirements of a Multi-Agent System (MAS).

---

## 1. Horizontal Decomposition (Current Implementation)
Horizontal decomposition breaks a task down by **process phase** or **functional layer**. 

### The Flow:
1. **Phase 1: Research** (Collect all data)
2. **Phase 2: Analysis** (Compare all data)
3. **Phase 3: Synthesis** (Write the entire report)

### Critical Challenges:
*   **Context Dilution**: By Phase 3, the "Summary of Research" contains a high-level abstraction of Phase 1. Specific, nuanced data points found in early research are often lost in the compaction process, leading to generic outputs.
*   **All-or-Nothing Failure**: If Phase 1 fails to collect a specific subset of data, Phase 2 and 3 are compromised across the entire objective.
*   **Cognitive Load**: The agent in Phase 3 must hold the "entire world" of the report in mind at once.

---

## 2. Vertical Decomposition (The Proposed Model)
Vertical decomposition breaks a task down by **sub-objective** or **feature vertical**, where each phase is a "finished" unit of work for a specific slice of the project.

### The Flow:
1. **Phase 1: Environmental Analysis** (Research -> Analyze -> Draft Environmental section)
2. **Phase 2: Social Analysis** (Research -> Analyze -> Draft Social section)
3. **Phase 3: Governance Analysis** (Research -> Analyze -> Draft Governance section)
4. **Phase 4: Final Integration** (Review coherence -> Generate PDF -> Deliver)

### Advantages:
1. **Tight Context Loops**: The agent works with a fresh, highly relevant context window for each specific topic. The transition from "Research" to "Draft" happens while the data is "hot."
2. **Incremental Verification**: Each phase produces a verifiable artifact. If the Social Analysis is poor, we only restart that phase, not the whole project.
3. **Specialist Affinity**: Vertical phases map better to specialized sub-agents. A "Finance Specialist" can handle an entire Finance vertical more effectively than just the "Finance Research" layer.
4. **Resilience**: Errors are isolated to the vertical they occur in.

---

## 3. Integration with the Universal Agent Harness
To support Vertical Decomposition, the **Harness Orchestrator** and **Interview Process** must evolve:

### A. Planning Prompt Evolution
The `PLANNING_SYSTEM_PROMPT` in `interview.py` should be instructed to:
- Identify "logical verticals" or "chapters" of the final deliverable.
- Group "Search", "Code", and "Write" tasks into a single phase per vertical.
- Use the `use_case` field to define the specific boundary of that vertical.

### B. Context Handover (The "Tread" Model)
Between phases, we must pass the "Tread"—the cumulative state.
- **Horizontal Tread**: Summary of everything found so far.
- **Vertical Tread**: A pointer to the completed file/artifact of the previous vertical to ensure stylistic consistency and prevent redundancy.

---

## 4. Conclusion & Next Steps
Vertical integration creates a more "human-like" workflow where we tackle one difficult problem at a time. While it may increase the total number of phases, it drastically reduces the "retry rate" of the massive multi-agent system by narrowing the scope of each execution turn.

**Recommended Action**: Refine the Planning Specialist's instructions to prioritize "topic-based" phase grouping over "action-based" grouping.
