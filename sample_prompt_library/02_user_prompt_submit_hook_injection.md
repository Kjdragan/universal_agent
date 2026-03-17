# UserPromptSubmit Hook Injection (Decomposition Prompt)

> This additional context is injected via the `UserPromptSubmit` hook
> on the **first** user message that has >10 words.
> It fires from `hooks.py:on_user_prompt_skill_awareness` (~line 1780).

---

### 🧭 Initial Task Assessment & Decomposition
Before beginning execution, decompose this request carefully:
1. **Analyze**: Break this request into atomic, logical steps.
2. **Happy Path Backbone**: Consider the deterministic path — your FIRST tool call should be productive work (e.g., `Task()` delegation, `mcp__composio__*` search, or discovery).
3. **Capability Match**: Evaluate your Capability Routing Doctrine. Route specific tasks to the appropriate specialized agents.
4. **Execution**: Proceed methodically, orchestrating subagents and validating each atomic step.

### ✅ Example: Research → Report → PDF → Email (Golden Path)
For a multi-part request like 'Search for X, create report, save as PDF, email it':
```
Step 1: Task(subagent_type='research-specialist', description='Research X', prompt='...')
   → Specialist calls COMPOSIO_SEARCH_NEWS, run_research_phase → refined_corpus.md
Step 2: Task(subagent_type='report-writer', description='Generate report from corpus', prompt='...')
   → Specialist calls run_report_generation → report.html
Step 3: mcp__internal__html_to_pdf (convert report.html → report.pdf)
Step 4: mcp__internal__upload_to_composio + COMPOSIO_GMAIL_SEND (email PDF)
```
Key: Start with Step 1 immediately. Do not call lifecycle tools (e.g., TaskStop) before you have started any tasks — there is nothing to stop yet.

---

## ⚠️ PROBLEM AREAS IDENTIFIED

### 1. TaskStop Mention (Line 1814 of hooks.py)
The last line of the golden path example says:
> "Do not call lifecycle tools (e.g., TaskStop) before you have started any tasks"

This **names TaskStop explicitly**, priming the model to think about it and use it.
Classic 'pink elephant' problem — mentioning the tool makes it salient.

### 2. prompt_builder.py Line 324-326
In section 7 (EXECUTION STRATEGY), the system prompt says:
> "Task lifecycle discipline: Only use SDK lifecycle tools when you have a concrete SDK-emitted task_id"

This also introduces the concept of 'lifecycle tools' and 'task_id' prematurely,
giving the model vocabulary to hallucinate with.

### 3. Capabilities Registry Volume
The capabilities.md is 1157 lines / ~69KB. This massive context may be causing
the model to 'warm up' by trying to demonstrate awareness of the system's tools
rather than immediately executing the user's request.
