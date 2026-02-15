---
name: agent_interview
description: A skill that allows the agent to conduct a structured or unstructured interview with the user to gather information.
---

# Agent Interview Skill

This skill allows you to interview the user to gather specific information, clarify ambiguous requirements, or build context (e.g., for user memory).

## Tools

### `fetch_context_gaps`

Retrieve pending questions or issues that have been logged by other agents.

- **No arguments**.
- Returns a list of pending gaps to address.

### `ask_user`

Ask a single question to the user and wait for their response.

- **question** (str): The question to ask.
- **category** (str, optional): A category for the question (e.g., "personal", "project", "preferences"). Defaults to "general".
- **options** (list[str], optional): A list of predefined options for the user to choose from.

### `finish_interview`

Call this when you have gathered all necessary information.

- **summary** (str): A brief summary of what was learned.
- **suggested_offline_tasks** (list[str], optional): A list of tasks (e.g., research topics, skill building) that the system should perform offline based on the interview results.

34: ## Standard Daily Protocol
35:
36: For regular daily interviews (e.g., 9:30 AM check-ins), follow this **Standard Daily Protocol** to ground the conversation:
37:
38: ### Phase 1: Goal Alignment
39: Start by grounding the user in their objectives. **ALWAYS** ask these questions first (unless the user explicitly skips):
40: - "What are your goals for **Today**?"
41: - "What are your goals for **This Week**?"
42: - "What are your goals for **This Month**?"
43:
44: ### Phase 2: Gap Resolution
45: After goals are set, check for pending issues logged by other agents.
46: - call `fetch_context_gaps` to retrieve pending questions.
47: - Address high-priority gaps first.
48:
49: ### Phase 3: Open Floor
50: Finally, give the user space to provide unstructured context.
51: - Ask: *"Is there anything else you'd like to discuss or add to our context?"*
52:
53: ## Usage Guidelines
54:
55: - **One Question at a Time**: Do not overload the user.
56: - **Dynamic Flow**: Adapt your questions based on previous answers, but stick to the protocol phases.
57: - **Identify Offline Work**: If the user mentions a topic that requires research or a new skill, add it to `suggested_offline_tasks` in `finish_interview`.
58: - **Closing the Loop**: Only call `finish_interview` after the user has had the final opportunity to speak.

## Example Workflow

1. Agent: `ask_user("What is your primary role on this project?")`
2. User: "I'm the lead architect."
3. Agent: `ask_user("Do you have a preferred programming style (e.g., functional, OOP)?")`
4. User: "I prefer functional python where possible."
5. ... (more questions) ...
6. Agent: `ask_user("Is there anything else you'd like to add?")`
7. User: "No, that covers it."
8. Agent: `finish_interview(summary="User is lead architect, prefers functional Python...")`

## Automated Scheduling

To ensure regular context updates, you can schedule a weekly interview check using the provided cron script.

```bash
uv run scripts/schedule_weekly_interview.py
```

This installs a cron job that runs weekly (Monday 9am) to check for pending gaps.
