## For Kevin

**TL;DR:** Anthropic navigated a significant regression in Claude Code quality (stemming from the Agent SDK harness, not the model). They have issued fixes, a public post-mortem, and reset usage limits. They are also actively rolling out reliability fixes (fewer hangs/stalls).

**The Shift:** This period highlights the fragility of the "harness"—the orchestration layer connecting the model to tools. For Universal Agent (UA), this confirms that custom tooling and "harness" logic are high-risk areas for regressions.

**Action Item:** Read the [official post-mortem](https://www.anthropic.com/engineering/april-23-postmortem) to understand how isolated system prompt changes broke the harness. Upgrade UA's internal testing to include "harness isolation" tests similar to what Anthropic is implementing internally.

## For UA

**Critical Update:** The stability issues reported (agent loops, quality slips) were caused by the **Agent SDK Harness**, not the Claude model itself.

**Implementation Takeaway:** UA relies on similar orchestration logic. The specific bugs listed (WebFetch stalling, Proxy 204 crashes, API 400 race conditions) are classic concurrency/state management issues in agent systems.

**Recommendation:** Review UA's error handling around web fetching and parallel requests. Specifically, ensure that `204 No Content` responses don't trigger logic that assumes a body exists, and that cache headers in parallel requests don't cause race conditions.
