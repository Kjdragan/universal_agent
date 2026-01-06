# Harness Planning Defaults

These defaults guide the agent's proactive planning when users submit vague or incomplete task requests.

## Core Tenets

When a user submits a task, they often haven't thought through:
1. **What format** they want the output in
2. **How** they want to receive/access it
3. **What scope** is appropriate
4. **What level of depth** they need

The agent should **proactively fill in sensible defaults** and only ask about things that truly require user input.

## Default Assumptions (Apply Unless User Specifies Otherwise)

### Output Format
| Task Type | Default Format | Rationale |
|-----------|----------------|-----------|
| Research | Markdown report with executive summary | Most useful for consumption |
| Data Analysis | Markdown with tables + source data files | Allows verification |
| Document Creation | PDF if formal, Markdown if working draft | Context-dependent |
| Code/Technical | Source files + README | Standard practice |

### Delivery Method
| Situation | Default | Rationale |
|-----------|---------|-----------|
| Long-running task (>1 hour) | Email notification on completion | User won't be watching |
| Short task | Save to workspace only | Immediate access |
| Recurring/scheduled task | Slack notification | Quick visibility |

### Scope & Depth
| Request Style | Assumed Scope | Depth |
|---------------|---------------|-------|
| "Research X" | Current news + background context | Moderate (500-1500 words) |
| "Deep dive on X" | Comprehensive, multiple sources | Deep (2000+ words) |
| "Quick update on X" | Last 7 days only | Brief (200-500 words) |
| "Report on X" | Structured analysis with sections | Moderate-Deep |

### Date Ranges
| Temporal Hint | Default Range |
|---------------|---------------|
| "recent", "latest" | Last 7 days |
| "current" | Last 30 days |
| No hint given | Last 30 days (unless historical topic) |

## When to Ask Questions

**ASK** when:
- Multiple valid interpretations exist (e.g., "report" could mean many things)
- User preference is genuinely unknown (e.g., email vs Slack for delivery)
- Scope could vary significantly (e.g., 3 countries vs 10 countries)
- Stakes are high (long-running process where wrong assumptions waste hours)

**DON'T ASK** when:
- A sensible default exists and user can refine later
- The request is specific enough to infer intent
- Asking would be pedantic or annoying

## Standard Interview Questions Template

When questions ARE needed, use this prioritization:

1. **Scope Clarification** (if ambiguous)
   - "This topic is broad. Should I focus on [A], [B], or both?"
   
2. **Output Format** (if multiple valid options)
   - "Would you prefer a detailed report or a quick summary?"
   
3. **Delivery Method** (for long-running tasks)
   - "Since this will take a while, would you like me to email you when done?"

4. **Related Extensions** (if obviously valuable)
   - "I noticed [related topic] is relevant. Should I include that too?"
