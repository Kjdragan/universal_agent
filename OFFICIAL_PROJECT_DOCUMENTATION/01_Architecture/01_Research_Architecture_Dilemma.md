# Research Architecture Dilemma: Deterministic Process vs. Dynamic Agility

**Date:** February 5, 2026
**Status:** DRAFT
**Context:** Analysis of Session `20260205_120858_5166ef05`

## 1. The Core Dilemma

We are facing a fundamental architectural tension in the Universal Agent:

* **Approach A: Deterministic Pipelines ("The Specialist")**
  * **Philosophy:** "Consistency above all."
  * **Mechanism:** User Query -> Delegate to Sub-Agent -> Run Standardized Pipeline (Search -> Crawl -> Refine -> Report).
  * **Pros:** Predictable, handles massive context well, produces standardized artifacts.
  * **Cons:** Slow, resource-intensive, rigid. A "sledgehammer" that treats every query like a nail.

* **Approach B: Dynamic Discovery ("The Skill")**
  * **Philosophy:** "Speed and Relevance."
  * **Mechanism:** User Query -> Pick Best Tool (`last30days`, `WebSearch`) -> Execute -> Answer.
  * **Pros:** Fast, agile, uses specialized skills effectively.
  * **Cons:** potentially chaotic, harder to measure/debug, relies on LLM to "figure it out" every time.

## 2. Case Study: The "Remotion" Run

**Session ID:** `session_20260205_120858_5166ef05`
**User Request:** "Use the last30days skill to research best practices for... Remotion"

### What Happened (The "Heavy" Path)

1. **Primary Agent**: Detected a research intent.
2. **Constraint Triggered**: System Prompt said "Delegate ALL research to `research-specialist`."
3. **Delegation**: Handed off to `research-specialist` (+31s).
4. **Redundancy**:
    * Primary Agent ran 4 searches (Time wasted).
    * Specialist ran 4 *more* searches (Time wasted).
5. **The "Sledgehammer"**: Specialist ran `run_research_pipeline`.
    * Crawled **46 URLs** (+30s).
    * Ran "Corpus Refinement" on 747 words.
    * **FAILURE**: The refinement model hallucinated/drifted (produced a summary about "Private Market Liquidity" instead of Remotion).
6. **Recovery**: Agent realized the summary was bad (Line 241) and fell back to raw search results.
7. **Total Time**: ~170 seconds (nearly 3 minutes).

### What Should Have Happened (The "Agile" Path)

1. **Primary Agent**: Detected "Last 30 Days" intent.
2. **Routing**: Recognized this as a "Trend" query, not a "Deep Dive".
3. **Execution**: Called `last30days` skill directly (or delegated to `trend-specialist`).
4. **Result**: 1 Script Run -> 1 Summary.
5. **Total Time**: ~30-45 seconds.

## 3. Analysis of "Deterministic" Failure Modes

The Deterministic approach failed here because it **over-processed** the data.

* **Crawl Overkill**: We don't need to crawl 46 pages to find "best practices". The search snippets often contain the answer.
* **Refinement Drift**: The "Refinement" step (summarizing crawled content) introduces a point of failure where the LLM can lose context or hallucinate if the crawled text is noisy.
* **Latency**: 3 minutes is too long for a chat answer.

## 4. The Path Forward: Dual-Track Architecture

We do not need to choose one or the other. We need **Routing**.

### The Solution: Smart Delegation

The Primary Agent must act as a **Router**, not just a **Delegator**.

| Feature | Track 1: Trend / Scout | Track 2: Deep Research |
| :--- | :--- | :--- |
| **Agent** | `Trend Specialist` | `Research Specialist` |
| **Trigger** | "News", "Latest", "Quick", "Overview", "30 Days" | "Report", "Deep Dive", "Codebase Analysis", "Complex" |
| **Tooling** | Agile Skills (`last30days`, `WebSearch`) | Heavy Pipeline (`crawl_parallel`, `refine_corpus`) |
| **Output** | Chat Message / Markdown | HTML Report / Comprehensive Doc |
| **Latency** | < 1 Minute | 5-10 Minutes |

### Implementation Strategy

We have already begun implementing this via the **Trend Specialist** role.

1. **Loosen Constraints**: The Primary Agent is no longer forced to use `research-specialist` for everything.
2. **Empower Skills**: The `Trend Specialist` is explicitly instructed to use *Skills* (like `last30days`) rather than *Pipelines*.
3. **Synergy**: Trend Research serves as the "Scout". If the Scout finds a rabbit hole, we *then* deploy the Heavy Machinery (Research Specialist).

## 5. Recommendation

**Adopt the Hybrid Model.**

* **Do not abandon the Deterministic Pipeline.** It is valuable for massive tasks (e.g., "Read this entire docs site").
* **Do not solely rely on Dynamic Agility.** It is prone to "lazy" answers for complex topics.
* **Use the Primary Agent's intelligence to Route.** Trust the model to classify "Quick/Trend" vs. "Deep/Heavy".

This approach maximizes efficiency (fast for simple) while preserving capability (deep for complex).

## 6. Deep Dive: The "Shadow Router" Problem

### 6.1 The "Suggestion" Trap
Upon deeper analysis of the `run.log`, we discovered that the "Deterministic Trap" is set *before* the agent even delegates to a specialist.

The culprit is the interactions between the **System Prompt** and the **Composio Tooling**:

1.  **System Prompt Bias**:
    *   The prompt explicitly instructs: *"For web/news research, ALWAYS use Composio search tools... You are FORBIDDEN from using these tools directly. You must DELEGATE to research-specialist."*
    *   This sets a mental model: "Research = Composio Search = Research Specialist".

2.  **Tool "Hijacking" (`COMPOSIO_SEARCH_TOOLS`)**:
    *   The agent calls `COMPOSIO_SEARCH_TOOLS` early in the process (often mistakenly, as it's supposed to delegate first).
    *   This tool does not just return search results; it returns **Authoritative Execution Guidance**.
    *   *Example from Log:* `"execution_guidance": "IMPORTANT: Follow the recommended plan below... Required Step 1: Perform a focused web search... Optional Step 2..."`
    *   This "Shadow Plan" effectively overrides the agent's own reasoning. It sees a "Required Plan" from a trusted tool and feels compelled to execute it via the heavy `research-specialist` path.

### 6.2 The Result: Reinforced Rigidity
This creates a self-reinforcing loop:
1.  **User**: "Last 30 days summary."
2.  **Agent**: "I need to research. System says use Composio."
3.  **Composio Tool**: "Here is a complex multi-step plan."
4.  **Agent**: "I must follow the plan. Delegate to Research Specialist."
5.  **Specialist**: "I have a complex task. Run the Pipeline."
6.  **Outcome**: 3-minute wait for a simple query.

### 6.3 Resolution Strategy
The Dual-Track Architecture solves this by **reclaiming the Routing Layer**:
1.  **System Prompt Update**: We explicitly defined a "Trend/Scout" track that bypasses the "Composio Search" mandate for trend queries.
2.  **Skill Primacy**: We instruct the agent to use *Skills* (`last30days`) as the primary mechanism for Trend queries, treating them as atomic actions rather than "Research Projects".

## 7. The Unified Planning Solution

### 7.1 The "Blind Planner" Gap
The user correctly identified that our "Planner" (the decomposition step) is broken because it is **tool-blind**.
- It knows about `COMPOSIO_SEARCH_WEB` because that's a registered Composio tool.
- It essentially "forgets" about `last30days` because that is a local, in-process skill, not a Composio App.

This leads to a plan that *only* uses Composio tools (Web, News), forcing the agent down the heavy path.

### 7.2 Strategy: Skill Injection into Planning
To fix this, we must ensure that any decomposition process (whether done by the Primary Agent or a Tool) is aware of **High-Value Local Skills**.

**Proposed Architecture:**
1.  **Skill Registration**: We treat `last30days` (and potentially future skills like `ExaSearch`) not just as "text instructions" in `SKILL.md`, but as **Atomic Capabilities** exposed to the Planner.
2.  **Prompt Update**: We modify the Primary Agent's prompt to say:
    > "When decomposing a request, consider these tools:
    > - Composio Search Tools (Web, News)
    > - **Local Skill: Last 30 Days (Trends)**
    > - **Local Skill: Browser (Debugging)**"
3.  **Unified Plan**: The Planner can then output a plan like:
    - Step 1: Use `last30days` to get an overview.
    - Step 2: Use `COMPOSIO_SEARCH_WEB` to fill in specific missing technical details.

This preserves the "Atomic Decomposition" framework the user values, while integrating the high-speed local skills into the menu of options.

## 8. The Data Contract Impedance Mismatch

### 8.1 The "Pipeline Break" Problem
The user correctly identified a critical flaw in simply "adding local skills" to the existing deterministic pipeline: **Output Compatibility**.

The current `Research Specialist` pipeline is a strictly typed system:
-   **Input**: Query
-   **Step 1 (Search/Crawl)**: Outputs `raw_crawl_data.json`
-   **Step 2 (Refine)**: Outputs `refined_corpus.md` (Structured Markdown)
-   **Step 3 (Report Writer)**: *Requires* `refined_corpus.md` to generate HTML.

**The `last30days` Skill**:
-   **Input**: Query
-   **Output**: Unstructured Text / Chat Summary (designed for immediate human consumption).
-   **Compatibility**: ❌ **Incompatible**. You cannot feed the text output of `last30days` into the `Report Writer` because it lacks the structured citations and corpus format the writer expects.

### 8.2 Why this Validates Dual-Track
This "Impedance Mismatch" effectively kills the idea of a "Single Unified Pipeline" where `last30days` is just another tool alongside Composio Search.
-   If we force `last30days` into the heavy pipeline, the pipeline breaks at the Report Writing stage.
-   If we try to make `last30days` output a `refined_corpus.md`, we cripple its speed and purpose (agility).

### 8.3 Conclusion: Parallel Architectures
Therefore, the **Dual-Track Architecture** is not just a routing preference; it is a **Technical Necessity**.

| Track | **Deep Research** (Existing) | **Trend Discovery** (New) |
| :--- | :--- | :--- |
| **Pipeline Type** | Linear, Strict, Deterministic | Agile, Loose, Probabilistic |
| **Data Contract** | `Crawl` → `Refined Corpus` → `HTML Report` | `Skill` → `Chat Output` |
| **Artifact** | Permanent Report (HTML/PDF) | Ephemeral Insight (Chat) |
| **Role** | "The Library" (Archive) | "The Newsroom" (Live) |

**Final Recommendation**:
Keep the pipelines separate. Use the **Planner** to decide *which pipeline to trigger*, but do not try to mix the *components* of the pipelines.
-   **Planner**: "This is a Trend request." -> Activate **Trend Pipeline**.
-   **Planner**: "This is a Deep Report request." -> Activate **Deep Pipeline**.

## 9. The Holy Grail: Unified Decomposition

### 9.1 The "Atomic Integration" Requirement
The user highlighted a crucial architectural goal: **We must not lose the Plannner.**
Even if we perform a "Quick & Dirty" research task (Trend Track), it should ideally be an **Atomic Step** within a larger, governed plan managed by the Composio Search Tools (the Planner).

**Why?**
1.  **Context**: A quick trend check might just be Step 1 of 5. If we bypass the Planner, we lose the map for Steps 2-5.
2.  **Determinism**: We want the *decision* to use `last30days` to be a deterministic output of the Planner ("I need trend data -> Use Trend Skill"), rather than a random guess by the agent.

### 9.2 The Solution: "Virtual Tool" Injection
To make the Planner (Composio Search Tools) "see" our local skills, we effectively need to register them as **Virtual Tools** in the planner's context.

**Mechanism:**
*   **Current State**: Planner sees `[COMPOSIO_SEARCH_WEB, COMPOSIO_SEARCH_NEWS, SERPAPI]`.
*   **Desired State**: Planner sees `[COMPOSIO_SEARCH_WEB, COMPOSIO_SEARCH_NEWS, SKILL_LAST_30_DAYS, SKILL_BROWSER_DEBUG]`.

**Implementation Path:**
1.  **Prompt Engineering**: We explicitly tell the Planner (via the System Prompt or the tool definition's `description`) that these local skills exist and should be treated as Atomic Actions.
2.  **Shadow Execution**: When the Planner outputs a plan saying "Run Step 1 using Skill Last 30 Days", our Agent intercepts that and runs the local Python script, then feeds the result back to the Planner as if a remote tool had executed.

This gives us the best of both worlds:
*   **Structure**: The Composio Planner maintains the high-level roadmap.
*   **Speed**: We execute the atomic steps using high-speed local skills.
