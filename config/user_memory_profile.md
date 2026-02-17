# User Memory Profile: Kevin Dragan

**Role & Background:**

- **Current Role:** CEO of Clearspring CG (Self-Employed)
- **Background:** Formerly venture debt investment firm
- **Current Focus:** AI Consulting and Agentic Systems Development

**Technical Stack & Preferences:**

- **Backend:** Python (FastAPI)
- **Frontend:** Next.js (AI-assisted)
- **IDE & Tools:** Antigravity IDE (Gemini 3 Pro + Opus 4.6), Windsurf, Codex extension (GPT 5.3-codex high)
- **Inference Strategy:** "Z-AI" coding plan emulating Anthropic models for cost-effective, high-volume token usage to enable always-on agentic processes.
- **Communication Style:** Adaptive ("Adapts to context" - quick when needed, thorough when exploring)

**Project Context:**

- **Universal Agent:** A multi-agent orchestration system (forked/evolved from Claudebot) utilizing Composio tools and router for OAuth connections. Deployed on VPS.
- **Goal:** Build a "Universal Agent" for business and personal processes that understands short/medium/long-term goals.
- **Freelancer Support Project:** An internal product to automate identifying, bidding, and executing freelance projects (Fiverr/Upwork).
- **Vision:** Autonomous agents for business automation leading to AI-powered SaaS products.

**Memory & System Requirements:**

- **Persistence:** File-based memory system is critical for cross-session context.
- **Reference Architecture:** Investigate OpenClaw/Clawbot for memory and heartbeat implementation patterns.
- **Functionality Parity:** Aiming for parity with OpenClaw features (heartbeat, scheduled runs) but adapted to the unique Universal Agent architecture.
- **Proactive Behavior:** The agent should work in the background (e.g., 2 AM) to generate projects, research, and briefings based on understanding the user's goals.

**Strategic Insight (The "Interview Skill" Vision):**

- **Periodic Interviews:** The interview skill should be run periodically (prop. weekly cron) to "health check" context and inject new priorities.
- **On-Demand:** User should be able to trigger interviews manually via system configuration.
- **Purpose:** Direct interviews are the fastest way to inject relevant context into memory, superior to passive observation.
