"""LLM-powered task classification service.

Provides intelligent classification for the proactive pipeline:
  - Priority classification (P0–P3) with contextual understanding
  - Agent routing (Simone / CODIE / ATLAS) based on task intent
  - Calendar event → actionable task description generation

Design principles:
  - Python plumbing drives the workflow (validation, fallback, caching)
  - LLM does the reasoning (understanding intent, context, nuance)
  - Deterministic heuristics as fast fallback when LLM is unavailable
  - JSON-structured output for reliable parsing
"""

from __future__ import annotations

import json
import os
import logging
from typing import Any, Optional
from universal_agent.utils.model_resolution import resolve_sonnet

logger = logging.getLogger(__name__)


# ── LLM Client Helper ──────────────────────────────────────────────────────

async def _get_anthropic_client():
    """Create an AsyncAnthropic client using the ZAI emulation layer."""
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise ClassificationError("anthropic package not installed") from exc

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise ClassificationError("No Anthropic API key available")

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url

    return AsyncAnthropic(**client_kwargs)


async def _call_llm(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    max_tokens: int = 512,
) -> str:
    """Make an LLM call and return the raw text response."""
    client = await _get_anthropic_client()

    response = await client.messages.create(
        model=model or resolve_sonnet(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    raw_text = ""
    for block in response.content:
        if hasattr(block, "text"):
            raw_text += block.text

    return raw_text.strip()


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Parse JSON from LLM response, stripping markdown fencing if present."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    return json.loads(cleaned)


class ClassificationError(Exception):
    """Raised when classification fails."""


# ── Priority Classification ─────────────────────────────────────────────────

_PRIORITY_SYSTEM = """\
You are a task priority classifier for an AI agent system. Given a task's title,
description, and source context, assign a priority level.

Priority levels:
- P0 (immediate): Direct operator instructions, system emergencies, time-critical
  actions that must happen NOW. Very rare.
- P1 (urgent): Deadlines today, urgent requests, follow-up threads from the
  operator, meeting prep for imminent meetings.
- P2 (normal): Standard work items, scheduled tasks, regular meetings,
  routine communications. This is the default for most work.
- P3 (background): Informational items, optional events, low-priority
  maintenance, "when you get a chance" tasks.

Consider:
- Implied urgency (a "Board Presentation" is P1 even without the word "urgent")
- Time sensitivity (events starting soon are higher priority)
- Sender trust (trusted operator tasks are elevated vs external requests)
- Context clues (meeting prep, deadlines, review requests)

Respond with ONLY a JSON object:
{
  "priority": "p0" | "p1" | "p2" | "p3",
  "reasoning": "1-sentence explanation"
}
"""

_PRIORITY_USER = """\
Classify the priority of this task:

Title: {title}
Description: {description}
Source: {source}
Sender trusted: {trusted}
Additional context: {context}
"""


async def classify_priority(
    *,
    title: str,
    description: str = "",
    source: str = "unknown",
    sender_trusted: bool = False,
    context: str = "",
    fallback_priority: int = 2,
) -> dict[str, Any]:
    """Classify task priority using LLM reasoning.

    Returns a dict with:
      - priority: int (0-3)
      - reasoning: str
      - method: "llm" or "fallback"

    Falls back to the provided fallback_priority if LLM is unavailable.
    """
    try:
        user_msg = _PRIORITY_USER.format(
            title=title,
            description=description[:500] or "(none)",
            source=source,
            trusted="yes" if sender_trusted else "no",
            context=context or "(none)",
        )

        raw = await _call_llm(system=_PRIORITY_SYSTEM, user=user_msg)
        result = _parse_json_response(raw)

        priority_str = str(result.get("priority", "p2")).lower().strip()
        priority_map = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
        priority = priority_map.get(priority_str, 2)

        return {
            "priority": priority,
            "reasoning": str(result.get("reasoning", "")),
            "method": "llm",
        }

    except Exception as exc:
        logger.warning("LLM priority classification failed, using fallback: %s", exc)
        return {
            "priority": fallback_priority,
            "reasoning": f"Fallback (LLM unavailable: {exc})",
            "method": "fallback",
        }


# ── Agent Routing ────────────────────────────────────────────────────────────

_ROUTING_SYSTEM = """\
You are a task routing judge for a multi-agent system. Given the user's natural
language task, decide which agent should handle it. Use judgment about the task's
actual objective; do not route by keyword counting.

Available agents:
- simone: Primary coordinator daemon. Handles email triage, calendar management,
  communication, brainstorming, coordination between agents, memory management,
  and anything ambiguous that needs human-like judgment.
- vp.coder.primary (CODIE): Specialized long-running coding agent. Handles
  repository work such as implementation, refactoring, debugging, tests,
  deployments, CI/CD, migrations, and infrastructure changes.
- vp.general.primary (ATLAS): General-abilities agent. Handles deep research,
  NotebookLM work, competitive analysis, content writing, market research,
  exploration, literature reviews, reports, artifacts, and investigative tasks.

Routing rules:
- If the task is clearly about writing, modifying, or debugging code in a repo → CODIE.
- If the task requires research, NotebookLM, analysis, content/artifact creation,
  or reporting → ATLAS.
- If the task requires Simone's personal context, communication judgment, or
  coordination across agents → Simone.
- If CODIE and ATLAS are both acceptable and one of them is available, choose an
  available VP rather than defaulting to Simone.
- Consider the intent, not just keywords. "Create a knowledge base about an
  agent" is research/content work for ATLAS unless the user asks to change code.
- "Review API docs" is research → ATLAS; "fix the API endpoint" is coding → CODIE.

Respond with ONLY a JSON object:
{
  "agent_id": "simone" | "vp.coder.primary" | "vp.general.primary",
  "confidence": "high" | "medium" | "low",
  "reasoning": "1-sentence explanation of why this agent"
}
"""

_ROUTING_USER = """\
Route this task to the best agent:

Title: {title}
Description: {description}
Labels: {labels}
Source: {source}
Project: {project}
Available agents: {available_agents}
"""


async def classify_agent_route(
    *,
    title: str,
    description: str = "",
    labels: list[str] | None = None,
    source_kind: str = "",
    project_key: str = "",
    available_agents: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Classify which agent should handle a task using LLM reasoning.

    Returns a dict with:
      - agent_id: str
      - confidence: str ("high", "medium", "low")
      - reasoning: str
      - method: "llm" or "fallback"
      - should_delegate: bool

    Falls back to "simone" if LLM is unavailable.
    """
    _AGENT_SIMONE = "simone"

    try:
        user_msg = _ROUTING_USER.format(
            title=title,
            description=(description[:500]) or "(none)",
            labels=", ".join(labels or []) or "(none)",
            source=source_kind or "(none)",
            project=project_key or "(none)",
            available_agents=", ".join(sorted(available_agents)) if available_agents else "simone, vp.coder.primary, vp.general.primary",
        )

        raw = await _call_llm(system=_ROUTING_SYSTEM, user=user_msg)
        result = _parse_json_response(raw)

        agent_id = str(result.get("agent_id", _AGENT_SIMONE)).strip()
        confidence = str(result.get("confidence", "medium")).lower()
        reasoning = str(result.get("reasoning", ""))

        # Validate agent_id
        valid_agents = {"simone", "vp.coder.primary", "vp.general.primary"}
        if agent_id not in valid_agents:
            agent_id = _AGENT_SIMONE

        # If the target agent isn't available, fall back to Simone
        if available_agents is not None and agent_id not in available_agents:
            reasoning = f"Target {agent_id} unavailable, falling back to Simone. Original: {reasoning}"
            agent_id = _AGENT_SIMONE
            confidence = "fallback"

        return {
            "agent_id": agent_id,
            "confidence": confidence,
            "reasoning": reasoning,
            "method": "llm",
            "should_delegate": agent_id != _AGENT_SIMONE,
        }

    except Exception as exc:
        logger.warning("LLM agent routing failed, defaulting to Simone: %s", exc)
        return {
            "agent_id": _AGENT_SIMONE,
            "confidence": "fallback",
            "reasoning": f"Fallback (LLM unavailable: {exc})",
            "method": "fallback",
            "should_delegate": False,
        }


# ── Calendar Task Description Generation ─────────────────────────────────────

_CALENDAR_DESC_SYSTEM = """\
You are a task preparation assistant. Given a calendar event, generate a concise,
actionable task description that tells an AI agent what to prepare or do before
the event.

Rules:
- Write 2-4 sentences maximum
- Focus on preparation actions, not just restating the event
- Consider the event type (meeting, review, presentation, social, etc.)
- Include relevant context from the description if available
- Be specific about what deliverables or prep work would be useful
- Do NOT include any scheduling information (that's handled separately)

Respond with ONLY a JSON object:
{
  "task_description": "...",
  "suggested_labels": ["label1", "label2"]
}
"""

_CALENDAR_DESC_USER = """\
Generate a task description for this calendar event:

Event title: {title}
Event description: {description}
Location: {location}
Attendees: {attendees}
Duration: {duration}
Organizer: {organizer}
"""


async def generate_calendar_task_description(
    *,
    title: str,
    description: str = "",
    location: str = "",
    attendees: list[str] | None = None,
    duration_minutes: int | None = None,
    organizer: str = "",
    fallback_description: str = "",
) -> dict[str, Any]:
    """Generate an actionable task description from a calendar event.

    Returns a dict with:
      - task_description: str
      - suggested_labels: list[str]
      - method: "llm" or "fallback"

    Falls back to the provided fallback_description if LLM is unavailable.
    """
    try:
        duration_str = f"{duration_minutes} minutes" if duration_minutes else "unknown"
        attendees_str = ", ".join(attendees or []) or "(none listed)"

        user_msg = _CALENDAR_DESC_USER.format(
            title=title,
            description=description[:500] or "(none)",
            location=location or "(none)",
            attendees=attendees_str,
            duration=duration_str,
            organizer=organizer or "(unknown)",
        )

        raw = await _call_llm(system=_CALENDAR_DESC_SYSTEM, user=user_msg)
        result = _parse_json_response(raw)

        return {
            "task_description": str(result.get("task_description", fallback_description)),
            "suggested_labels": list(result.get("suggested_labels", [])),
            "method": "llm",
        }

    except Exception as exc:
        logger.warning("LLM calendar description generation failed, using fallback: %s", exc)
        return {
            "task_description": fallback_description,
            "suggested_labels": [],
            "method": "fallback",
        }


# ── Temporal Extraction (due_at) ─────────────────────────────────────────────

_TEMPORAL_SYSTEM = """\
You are a temporal reference extractor. Given an email subject and body, determine
if the sender is requesting something at a specific date and/or time.

Current date and time (Central Time): {current_datetime_ct}

Rules:
- Only extract times that represent WHEN a task should be executed or a deadline.
- Do NOT extract times that are just mentioned in passing or as context.
- "at 9:50am" → extract 9:50 AM today (or tomorrow if 9:50 AM has already passed)
- "by 3pm tomorrow" → extract 3:00 PM tomorrow
- "March 28 at noon" → extract noon on March 28
- "tonight" → extract 8:00 PM today
- "ASAP" or "now" → do NOT extract (these are immediate, no scheduling needed)
- If no specific time is mentioned, return null.
- All times are in Central Time (America/Chicago) unless explicitly stated otherwise.
- If a time has already passed today, assume it means tomorrow at that time.

Respond with ONLY a JSON object:
{{
  "has_time": true | false,
  "iso_datetime": "YYYY-MM-DDTHH:MM:SS-05:00" | null,
  "reasoning": "1-sentence explanation",
  "confidence": "high" | "medium" | "low"
}}
"""

_TEMPORAL_USER = """\
Extract any temporal reference from this email:

Subject: {subject}
Body: {body}
"""


async def extract_due_at(
    *,
    subject: str,
    body: str = "",
    current_datetime_ct: str = "",
) -> dict[str, Any]:
    """Extract a due_at timestamp from email text using LLM reasoning.

    Returns a dict with:
      - due_at: str | None (ISO-8601 datetime in Central Time, or None)
      - reasoning: str
      - confidence: str ("high", "medium", "low")
      - method: "llm" or "fallback"
    """
    # Quick heuristic pre-check: skip LLM call if there's clearly no time
    # reference at all (saves API cost for most emails)
    import re
    combined = f"{subject} {body}".lower()
    _quick_time_check = re.compile(
        r'\b(\d{1,2}:\d{2}|\d{1,2}\s*(am|pm)|noon|midnight|tonight|'
        r'tomorrow|next\s+(mon|tue|wed|thu|fri|sat|sun)|'
        r'by\s+\d|at\s+\d|before\s+\d|until\s+\d|'
        r'january|february|march|april|may|june|july|august|'
        r'september|october|november|december)\b',
        re.IGNORECASE,
    )
    if not _quick_time_check.search(combined):
        return {
            "due_at": None,
            "reasoning": "No temporal references detected in text",
            "confidence": "high",
            "method": "heuristic_skip",
        }

    try:
        # Determine current datetime in Central Time
        if not current_datetime_ct:
            from datetime import datetime
            import pytz
            ct = pytz.timezone("America/Chicago")
            current_datetime_ct = datetime.now(ct).strftime("%Y-%m-%d %I:%M %p %Z")

        system_prompt = _TEMPORAL_SYSTEM.format(current_datetime_ct=current_datetime_ct)
        user_msg = _TEMPORAL_USER.format(
            subject=subject,
            body=(body[:1000]) or "(empty)",
        )

        raw = await _call_llm(
            system=system_prompt,
            user=user_msg,
            max_tokens=256,
        )
        result = _parse_json_response(raw)

        has_time = bool(result.get("has_time", False))
        iso_datetime = result.get("iso_datetime") if has_time else None
        reasoning = str(result.get("reasoning", ""))
        confidence = str(result.get("confidence", "medium")).lower()

        # Validate the ISO datetime if present
        if iso_datetime:
            from datetime import datetime as _dt
            try:
                _dt.fromisoformat(str(iso_datetime))
            except (ValueError, TypeError):
                logger.warning("LLM returned invalid ISO datetime: %s", iso_datetime)
                iso_datetime = None

        return {
            "due_at": str(iso_datetime) if iso_datetime else None,
            "reasoning": reasoning,
            "confidence": confidence,
            "method": "llm",
        }

    except Exception as exc:
        logger.warning("LLM temporal extraction failed (non-fatal): %s", exc)
        return {
            "due_at": None,
            "reasoning": f"Fallback (LLM unavailable: {exc})",
            "confidence": "fallback",
            "method": "fallback",
        }

# ── Disjointed Task Extraction ───────────────────────────────────────────────

_DISJOINTED_TASK_SYSTEM = """\
You are an intelligent email analyzer for an AI agent system. Your job is to read a message and determine if it contains multiple, independent tasks or requests that should be processed separately.

Rules:
- If the email is a single cohesive task or a set of tightly coupled steps for one goal, treat it as 1 task.
- If the email contains clearly disjointed or unrelated requests (e.g. "Also, can you look into X", or "1. Fix X. 2. Write a blog about Y."), split them into separate tasks.
- Return a list where each item represents an actionable, decoupled task derived from the email.
- The task should contain both the goal and the necessary original context from the email so the agent handling it has all the information they need without needing the other tasks.

Respond with ONLY a JSON object:
{
  "tasks": [
    {
      "task_content": "Full description of the task, preserving original context and constraints",
      "reasoning": "1-sentence explanation of why this is considered a distinct task"
    }
  ]
}
"""

_DISJOINTED_TASK_USER = """\
Extract actionable tasks from this email:

Subject: {subject}
Body: {body}
"""

async def extract_disjointed_tasks(
    *,
    subject: str,
    body: str = "",
) -> list[dict[str, Any]]:
    """Analyze an email to extract disjointed, independent tasks.

    Returns a list of dicts, each with:
      - task_content: str
      - reasoning: str
    If no tasks are found or extraction fails, returns a single task with the original body.
    """
    try:
        user_msg = _DISJOINTED_TASK_USER.format(
            subject=subject,
            body=(body[:3000]) or "(empty)",
        )

        raw = await _call_llm(
            system=_DISJOINTED_TASK_SYSTEM,
            user=user_msg,
            max_tokens=1024,
        )
        result = _parse_json_response(raw)

        tasks = result.get("tasks", [])
        if not tasks or not isinstance(tasks, list):
            # Fallback to single task if extraction is malformed
            return [{"task_content": f"Subject: {subject}\n\n{body}", "reasoning": "Fallback full body"}]

        return tasks

    except Exception as exc:
        logger.warning("LLM disjointed task extraction failed (non-fatal): %s", exc)
        return [{"task_content": f"Subject: {subject}\n\n{body}", "reasoning": f"Fallback (LLM unavailable: {exc})"}]
