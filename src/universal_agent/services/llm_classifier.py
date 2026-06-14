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
import logging
import os
from typing import Any, Optional, TypedDict

from universal_agent.utils.model_resolution import (
    resolve_haiku,
    resolve_opus,
    resolve_sonnet,
)

logger = logging.getLogger(__name__)


# ── Typed Return Contracts ──────────────────────────────────────────────────


class PriorityResult(TypedDict):
    """Return shape of classify_priority."""

    priority: int
    reasoning: str
    method: str


class AgentRouteResult(TypedDict):
    """Return shape of classify_agent_route."""

    agent_id: str
    confidence: str
    reasoning: str
    method: str
    should_delegate: bool


class CalendarTaskResult(TypedDict):
    """Return shape of generate_calendar_task_description."""

    task_description: str
    suggested_labels: list[str]
    method: str


class TemporalResult(TypedDict):
    """Return shape of extract_due_at."""

    due_at: str | None
    reasoning: str
    confidence: str
    method: str


class DisjointedTask(TypedDict):
    """Single item in the list returned by extract_disjointed_tasks."""

    task_content: str
    reasoning: str


class TutorialBuildabilityResult(TypedDict):
    """Return shape of classify_tutorial_buildability."""

    buildable: bool
    reasoning: str
    method: str


# ── LLM Client Helper ──────────────────────────────────────────────────────

def _classifier_default_model() -> str:
    """Default model for the classification/extraction/routing wrappers.

    These are bounded classification tasks, not flagship reasoning — the
    tiering doctrine (10_zai_rate_limiter.md §7, 14_model_tiering_by_process)
    has always said they belong below opus; they only ran on glm-5.1 because
    `_call_llm`'s fallback default is `resolve_opus()`. The sonnet tier
    (glm-5-turbo) was A/B-proven equal to the opus default on the convergence
    judge, and live per-model 429 data (2026-06-11) showed ZAI throttling
    glm-5.1 at ~85%+ while glm-5-turbo flowed clean. Override via
    UA_LLM_CLASSIFIER_DEFAULT_MODEL.
    """
    return (os.getenv("UA_LLM_CLASSIFIER_DEFAULT_MODEL") or "").strip() or resolve_sonnet()


def _limiter_enabled() -> bool:
    """Is the ZAIRateLimiter routing for `_call_llm` enabled?

    Default OFF. Flip via `UA_LLM_CLASSIFIER_LIMITER_ENABLED` (Infisical for
    prod; never the VPS `.env` — deploys wipe it; durable default-on means a
    code-default change here).
    """
    from universal_agent.feature_flags import _is_truthy

    return _is_truthy(os.getenv("UA_LLM_CLASSIFIER_LIMITER_ENABLED"))


def _targets_zai(base_url: Optional[str]) -> bool:
    """Does this call's EFFECTIVE base URL point at the ZAI proxy?

    The limiter protects the ZAI account; `_call_llm`'s ``base_url`` override
    (the per-stage A/B knobs) can route a call to real Anthropic, whose 429s
    are unrelated — wrapping those would consume ZAI tier slots and poison
    ZAI tier state. Mirrors `zai_observability.ZAI_HOSTS` filtering.
    """
    effective = (base_url or os.getenv("ANTHROPIC_BASE_URL") or "").strip()
    if not effective:
        return False  # default Anthropic endpoint — not ZAI
    from universal_agent.services.zai_observability import ZAI_HOSTS

    return any(h in effective for h in ZAI_HOSTS)


def _limiter_call_budget_seconds() -> Optional[float]:
    """Wall-clock budget for one logical `_call_llm` retry saga when routed
    through the limiter. Keeps the worst case (5 attempts + backoffs) inside
    cron budgets (convergence boxes whole runs at 600s)."""
    try:
        value = float(os.getenv("UA_LLM_CLASSIFIER_LIMITER_BUDGET_SECONDS", "300") or "300")
    except (TypeError, ValueError):
        return 300.0
    return value if value > 0 else None


async def _get_anthropic_client(
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    max_retries: Optional[int] = None,
) -> Any:
    """Create an AsyncAnthropic client using the ZAI emulation layer.

    ``base_url`` / ``api_key`` override the shared env defaults for a single
    call — used by per-stage A/B knobs (e.g. routing the convergence cluster
    judge to a different provider/model). When unset, the usual env chain
    (the ZAI proxy) is used. ``max_retries`` overrides the env default —
    the limiter-routed path passes 0 so retry policy lives in ONE layer
    (SDK-internal retries would double every wire 429 and sleep inside the
    acquired slot; same pairing `mission_control_chief_of_staff.py` uses).
    """
    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise ClassificationError("anthropic package not installed") from exc

    api_key = api_key or (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise ClassificationError("No Anthropic API key available")

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = base_url or os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url

    # Bound every call. The Anthropic SDK default is a 600s/attempt timeout with
    # 2 retries — far too long for our cron budgets: a single stalled upstream
    # (e.g. the ZAI proxy under fair-usage throttling) could hang a caller for
    # up to ~30 min. Cap it so a stall fails fast and the caller can move on.
    # Default raised 60s -> 180s (2026-06-03): the 60s cap was shorter than the
    # ZAI/glm latency tail for large prompts (e.g. convergence triage), so calls
    # timed out and the convergence promoter stalled. Env-overridable.
    try:
        client_kwargs["timeout"] = float(os.getenv("UA_LLM_CALL_TIMEOUT_SECONDS", "180") or "180")
    except (TypeError, ValueError):
        client_kwargs["timeout"] = 180.0
    if max_retries is not None:
        client_kwargs["max_retries"] = max_retries
    else:
        try:
            client_kwargs["max_retries"] = int(os.getenv("UA_LLM_CALL_MAX_RETRIES", "1") or "1")
        except (TypeError, ValueError):
            client_kwargs["max_retries"] = 1

    return AsyncAnthropic(**client_kwargs)


async def _call_llm(
    *,
    system: str,
    user: str,
    model: Optional[str] = None,
    max_tokens: int = 1024,  # doubled from 512 per audit
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: Optional[float] = None,
) -> str:
    """Make an LLM call and return the raw text response.

    ``temperature`` (default ``None``) is threaded into ``messages.create`` ONLY
    when set, so every existing caller is byte-unchanged (no ``temperature`` key =
    the provider default, ~1.0). Judgment gates pass ``temperature=0`` (via
    :func:`_resolve_judge_temperature`) to make their verdicts DETERMINISTic — a
    live probe found ``_call_llm``'s missing temperature was the single source of
    ~40% per-item coin-flip across the triage/buildability/cluster gates (n=10:
    60% self-agreement at the default temperature -> 100% at ``temperature=0``).

    Closes the per-call ``AsyncAnthropic`` client deterministically (``try/finally``)
    so its underlying httpx ``AsyncClient`` is shut down inside the live event loop.
    Without this, callers reached via the ``nest_asyncio`` + ``loop.run_until_complete``
    seam (proactive convergence's clustering + ideation sweeps) leaked one unclosed
    client per call; their ``aclose()`` finalizers then fired AFTER the cron's
    ``asyncio.run()`` loop closed, spraying ~12 ``RuntimeError('Event loop is closed')``
    per run into the journal (non-fatal, but log noise + connection leak).

    When ``UA_LLM_CLASSIFIER_LIMITER_ENABLED`` is on AND the effective base URL
    targets the ZAI proxy, the call routes through
    ``rate_limiter.with_rate_limit_retry`` — one edit covering every `_call_llm`
    caller (~12 flows) with the per-tier AIMD limiter: coordinated backoff,
    tier-bucketed concurrency (via ``model_id_to_tier``), and the FUP cliff
    stop. The SDK's own retries are disabled on that path (``max_retries=0``)
    so retry policy lives in one layer. Flag OFF or non-ZAI target → the
    original direct path, unchanged.
    """
    resolved_model = model or resolve_opus()
    use_limiter = _limiter_enabled() and _targets_zai(base_url)
    client = await _get_anthropic_client(
        base_url=base_url,
        api_key=api_key,
        max_retries=0 if use_limiter else None,
    )
    # Shared create() kwargs for BOTH paths. ``temperature`` is added only when
    # set so callers that don't pass it keep their exact prior wire payload.
    create_kwargs: dict[str, Any] = dict(
        model=resolved_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if temperature is not None:
        create_kwargs["temperature"] = temperature
    try:
        if use_limiter:
            from universal_agent.rate_limiter import with_rate_limit_retry
            from universal_agent.utils.model_resolution import model_id_to_tier

            response = await with_rate_limit_retry(
                client.messages.create,
                context="llm_classifier",
                model_tier=model_id_to_tier(resolved_model),
                max_total_seconds=_limiter_call_budget_seconds(),
                **create_kwargs,
            )
        else:
            response = await client.messages.create(**create_kwargs)

        raw_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw_text += block.text

        return raw_text.strip()
    finally:
        # Never let client teardown mask the call's result or its exception.
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass


def _resolve_judge_temperature(env_name: Optional[str] = None) -> Optional[float]:
    """Resolve the determinism temperature for a judgment gate.

    Reads a per-gate override env (e.g. ``UA_INTEL_TRIAGE_TEMPERATURE``) first,
    then the shared ``UA_LLM_JUDGE_TEMPERATURE``. Returns ``None`` when neither is
    set (today's behavior — no temperature passed, provider default ~1.0). Set
    either to ``0`` to make the gate deterministic. A non-numeric value is
    ignored (falls through to the next source / ``None``), never raising.
    """
    for name in (env_name, "UA_LLM_JUDGE_TEMPERATURE"):
        if not name:
            continue
        raw = (os.getenv(name) or "").strip()
        if not raw:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _coerce_score(raw: Any) -> Optional[float]:
    """Coerce an LLM-emitted graded score into a clamped ``0..100`` float, or
    ``None`` when the value is missing/non-numeric (an un-decidable verdict the
    caller fails closed, exactly like an out-of-vocab categorical verdict)."""
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    if score != score:  # NaN guard
        return None
    return max(0.0, min(100.0, score))


def _parse_json_response(raw: str) -> dict[str, Any]:
    """Parse JSON from an LLM response, tolerating fencing and trailing junk.

    Strict happy path first: a clean object (fenced or not) parses exactly as
    before. Only on failure do we recover the FIRST valid JSON *object* via
    ``raw_decode`` -- LLMs (notably the ZAI/glm refine path) intermittently
    append a duplicate object or a trailing prose sentence after the JSON, which
    made the old strict ``json.loads`` raise ``Extra data: ...`` and discard an
    otherwise-valid verdict. We scan successive ``{`` offsets so leading prose
    containing a stray brace cannot defeat recovery. Genuinely non-JSON input
    still raises ``json.JSONDecodeError`` (no decodable object), preserving the
    strict contract relied on by callers and existing tests.
    """
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_err:
        decoder = json.JSONDecoder()
        start = cleaned.find("{")
        while start != -1:
            try:
                obj, _end = decoder.raw_decode(cleaned, start)
            except json.JSONDecodeError:
                start = cleaned.find("{", start + 1)
                continue
            if isinstance(obj, dict):
                return obj
            start = cleaned.find("{", start + 1)
        # Nothing decoded to a dict -- preserve the strict contract.
        raise first_err


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
) -> PriorityResult:
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

        raw = await _call_llm(
            system=_PRIORITY_SYSTEM, user=user_msg, model=_classifier_default_model()
        )
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
) -> AgentRouteResult:
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

        raw = await _call_llm(
            system=_ROUTING_SYSTEM, user=user_msg, model=_classifier_default_model()
        )
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
) -> CalendarTaskResult:
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

        raw = await _call_llm(
            system=_CALENDAR_DESC_SYSTEM, user=user_msg, model=_classifier_default_model()
        )
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
) -> TemporalResult:
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
            model=_classifier_default_model(),
            max_tokens=512,  # doubled from 256 per audit
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
) -> list[DisjointedTask]:
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
            model=_classifier_default_model(),
            max_tokens=2048,  # doubled from 1024 per audit
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


# ── Tutorial Buildability Judge ──────────────────────────────────────────────

_TUTORIAL_BUILDABILITY_SYSTEM = """\
You decide whether a YouTube video is a coding tutorial from which an autonomous
coding agent could build a small but working code demo.

Inputs you receive: the video title, the channel name, and a Claude-distilled
summary of the actual transcript (~1-2 paragraphs). The summary is your primary
signal — the title and channel are context, not evidence.

Return buildable=true ONLY when ALL of the following are true:
- The video teaches or demonstrates concrete software functionality (writing code,
  building an agent/script/tool, integrating an API/SDK, configuring a framework,
  etc.).
- A reasonable engineer could replicate a working artifact (script, repo, demo,
  notebook) from what the summary describes — even if the exact code isn't shown.
- The summary is technical and specific, not a high-level news/opinion/commentary
  piece that merely *mentions* tech.

Return buildable=false when ANY of the following is true:
- The summary is news, current events, geopolitics, sports, music, comedy, vlog,
  reaction, drama, podcast chat, interview without concrete tech demo, product
  announcement without implementation detail, or general commentary.
- The summary is empty, vague, or lacks any concrete software/implementation
  detail an agent could act on.
- The video is *about* technology but doesn't demonstrate buildable functionality
  (e.g., "the future of AI", company news, opinion piece).

Be strict. False positives create wasted work for a downstream coding agent.
When uncertain, return false.

Respond with ONLY a JSON object:
{
  "buildable": true | false,
  "reasoning": "1-2 sentences citing specific evidence from the summary"
}
"""

_TUTORIAL_BUILDABILITY_USER = """\
Title: {title}
Channel: {channel}

Transcript summary:
{summary}
"""

# Batched twin of _TUTORIAL_BUILDABILITY_SYSTEM (PR P3). Many videos judged in one
# structured-output call, keyed by index. Same strict bar per video.
_TUTORIAL_BUILDABILITY_BATCH_SYSTEM = _TUTORIAL_BUILDABILITY_SYSTEM + """

BATCH MODE: You are given MULTIPLE videos AT ONCE in a `videos` array, each with a
numeric `index`. Judge EACH video INDEPENDENTLY by the rules above, applying the
SAME strict bar to every one — do NOT become lax because there are many videos.
Return ONLY a JSON object with one verdict per index, covering EVERY index you were
given:
{"verdicts":[{"index":0,"buildable":true,"reasoning":"1-2 sentences citing the summary"}]}
"""

# ── Graded variant (PR: graded-judge redesign) ──────────────────────────────
# Asks for a 0-100 buildability SCORE instead of a binary buildable/not, so a
# code-side threshold (UA_TUTORIAL_BUILD_THRESHOLD) can be set HIGH to suppress
# the false positives the P3 batched judge leaned toward. Activated only when the
# threshold is set; default (unset) keeps the binary prompt above. Rubric anchored
# to spread the score band.
_TUTORIAL_BUILDABILITY_GRADED_SYSTEM = """\
You decide HOW BUILDABLE a YouTube video is as a coding tutorial — whether an
autonomous coding agent could build a small but working code demo from it — and
return a 0-100 score.

Inputs: the video title, the channel name, and a Claude-distilled summary of the
actual transcript (~1-2 paragraphs). The summary is your primary signal; the
title and channel are context, not evidence.

Judge THREE sub-dimensions, then COMBINE into one score:
- CONCRETE FUNCTIONALITY: does it teach/demonstrate concrete software (writing
  code, building an agent/script/tool, integrating an API/SDK, configuring a
  framework)? News/opinion/commentary that merely *mentions* tech scores low.
- REPLICABILITY: could a reasonable engineer rebuild a working artifact (script,
  repo, demo, notebook) from what the summary describes, even if the exact code
  isn't shown? Vague "the future of X" framing scores low.
- SPECIFICITY: is the summary technical and specific (named tools, concrete
  steps), versus high-level?

Anchor the combined 0-100 score:
- 85-100: clearly a buildable coding tutorial — specific tools, replicable steps.
- 70-84: buildable, with one soft dimension (e.g. light on exact steps).
- 50-69: borderline — technical but thin on what to actually build.
- 25-49: weak — about tech but no buildable functionality (news/opinion/demo-less).
- 0-24: not technical / not buildable (vlog, reaction, podcast chat, announcement).

Be strict: false positives create wasted work for a downstream coding agent. Do
NOT default to a round number; if torn between two bands pick a SPECIFIC value
inside one (e.g. 62 or 78 — never a flat 70 or 75).

Respond with ONLY a JSON object:
{"score": <integer 0-100>, "reasoning": "1-2 sentences citing specific evidence from the summary"}
"""

_TUTORIAL_BUILDABILITY_GRADED_BATCH_SYSTEM = _TUTORIAL_BUILDABILITY_GRADED_SYSTEM + """

BATCH MODE: You are given MULTIPLE videos AT ONCE in a `videos` array, each with a
numeric `index`. SCORE EACH video INDEPENDENTLY by the rubric above, applying the
SAME anchors to every one — do NOT become lax because there are many videos.
Return ONLY a JSON object with one verdict per index, covering EVERY index you were
given:
{"verdicts":[{"index":0,"score":<integer 0-100>,"reasoning":"1-2 sentences citing the summary"}]}
"""


async def classify_tutorial_buildability(
    *,
    title: str,
    channel_name: str = "",
    summary_text: str = "",
    model: Optional[str] = None,
) -> "TutorialBuildabilityResult":
    """Ask the LLM whether a video is a code-buildable tutorial.

    Returns a dict with:
      - buildable: bool
      - reasoning: str
      - method: "llm" or "fallback"

    On any error returns method="fallback" with buildable=False — the caller
    should treat a fallback verdict as "skip, do not cache, retry next time".
    """
    try:
        user_msg = _TUTORIAL_BUILDABILITY_USER.format(
            title=title or "(unknown)",
            channel=channel_name or "(unknown)",
            summary=(summary_text or "").strip()[:4000] or "(empty)",
        )
        # Binary buildable/not judgment — Haiku tier (glm-4.5-air) by default;
        # override with UA_TUTORIAL_BUILDABILITY_MODEL. When UA_TUTORIAL_BUILD_THRESHOLD
        # is set, switch to the graded 0-100 score + code-side cutoff (set HIGH).
        threshold = _tutorial_build_threshold()
        graded = threshold is not None
        system = _TUTORIAL_BUILDABILITY_GRADED_SYSTEM if graded else _TUTORIAL_BUILDABILITY_SYSTEM
        temperature = _resolve_judge_temperature("UA_TUTORIAL_BUILD_TEMPERATURE")
        raw = await _call_llm(
            system=system,
            user=user_msg,
            max_tokens=400,
            model=model or (os.getenv("UA_TUTORIAL_BUILDABILITY_MODEL") or "").strip() or resolve_haiku(),
            temperature=temperature,
        )
        parsed = _parse_json_response(raw)
        reasoning = str(parsed.get("reasoning") or "").strip()
        if graded:
            # Missing/garbled score ⇒ fail closed to not-buildable (method='llm',
            # cacheable) — identical conservatism to a malformed binary verdict.
            score = _coerce_score(parsed.get("score"))
            buildable = score is not None and score >= threshold
            return {"buildable": buildable, "reasoning": reasoning, "method": "llm"}
        buildable = bool(parsed.get("buildable", False))
        return {"buildable": buildable, "reasoning": reasoning, "method": "llm"}
    except Exception as exc:
        logger.warning("LLM tutorial-buildability judge failed: %s", exc)
        return {
            "buildable": False,
            "reasoning": f"Fallback (LLM unavailable: {exc})",
            "method": "fallback",
        }


def _tutorial_buildability_batch_size() -> int:
    """Videos per batched buildability call. Default **1 == legacy per-video
    path** (the batched judge stays OFF until a live batched-vs-per-item A/B holds
    — the win is concentrated on cold-cache/backfill, small at steady state).
    ``UA_TUTORIAL_BUILDABILITY_BATCH_SIZE``, clamped [1, 60]."""
    try:
        n = int(os.getenv("UA_TUTORIAL_BUILDABILITY_BATCH_SIZE", "1") or "1")
    except (TypeError, ValueError):
        n = 1
    return max(1, min(60, n))


def _tutorial_build_threshold() -> Optional[int]:
    """Graded buildability cutoff. ``UA_TUTORIAL_BUILD_THRESHOLD`` (0-100).

    UNSET (default) ⇒ the legacy binary buildable/not judgment (byte-identical to
    today, PR inert). SET ⇒ the judge emits a 0-100 score and ``buildable =
    score >= this``. Lean HIGH — the P3 batched-vs-per-item divergence was
    false-positive-leaning and a high cutoff directly suppresses weak 'buildable'
    calls. A non-numeric value is treated as unset."""
    raw = (os.getenv("UA_TUTORIAL_BUILD_THRESHOLD") or "").strip()
    if not raw:
        return None
    try:
        return max(0, min(100, int(float(raw))))
    except (TypeError, ValueError):
        return None


async def classify_tutorial_buildability_batched(
    items: list[dict[str, Any]],
    *,
    model: Optional[str] = None,
    batch_size: Optional[int] = None,
    deadline: Optional[float] = None,
    stats: Optional[dict[str, Any]] = None,
) -> dict[str, "TutorialBuildabilityResult"]:
    """Batched twin of :func:`classify_tutorial_buildability` over many videos.

    ``items``: ``[{video_id, title, channel_name, summary_text}]``. Returns
    ``{video_id: {buildable, reasoning, method}}`` for every item with a non-empty
    summary (empty-summary items are dropped — never judged, never cached, exactly
    like the single-video path's ``return False`` short-circuit). Built on the
    shared :func:`batched_judge` helper: one structured-output call judges up to
    ``batch_size`` videos at the Haiku tier.

    Per-item ``method`` semantics match the single-video path EXACTLY so the
    caller's cache rule is byte-identical: a video the batch judged →
    ``method='llm'`` (CACHE it, buildable True or False); a whole-chunk failure /
    missing verdict → ``method='fallback'`` (skip + do NOT cache + retry next
    sweep). A Fair-Usage signal trips ``batched_judge``'s one-shot breaker
    (remaining videos stay 'fallback')."""
    from universal_agent.services.batched_judge import batched_judge

    valid = [it for it in items if str((it or {}).get("summary_text") or "").strip()]
    if not valid:
        return {}

    resolved_model = (
        model
        or (os.getenv("UA_TUTORIAL_BUILDABILITY_MODEL") or "").strip()
        or resolve_haiku()
    )
    bs = batch_size if batch_size is not None else _tutorial_buildability_batch_size()
    # Graded vs binary — the SAME switch as the single-video classify path.
    threshold = _tutorial_build_threshold()
    graded = threshold is not None
    temperature = _resolve_judge_temperature("UA_TUTORIAL_BUILD_TEMPERATURE")
    system = _TUTORIAL_BUILDABILITY_GRADED_BATCH_SYSTEM if graded else _TUTORIAL_BUILDABILITY_BATCH_SYSTEM

    def build_prompt(chunk: list[dict[str, Any]]) -> str:
        return json.dumps(
            {
                "videos": [
                    {
                        "index": i,
                        "title": str(it.get("title") or "(unknown)"),
                        "channel": str(it.get("channel_name") or "(unknown)"),
                        "summary": (str(it.get("summary_text") or "").strip()[:4000] or "(empty)"),
                    }
                    for i, it in enumerate(chunk)
                ]
            },
            ensure_ascii=True,
        )

    def parse(item: dict[str, Any], verdict: dict[str, Any]) -> "TutorialBuildabilityResult":
        # A present verdict (even a malformed one defaulting to not-buildable) is a
        # real judgement → method='llm' (cacheable) — identical to the single path.
        if graded:
            score = _coerce_score(verdict.get("score"))
            buildable = score is not None and score >= threshold
            return {
                "buildable": buildable,
                "reasoning": str(verdict.get("reasoning") or "").strip(),
                "method": "llm",
            }
        return {
            "buildable": bool(verdict.get("buildable", False)),
            "reasoning": str(verdict.get("reasoning") or "").strip(),
            "method": "llm",
        }

    fail_closed: "TutorialBuildabilityResult" = {
        "buildable": False,
        "reasoning": "Fallback (batch unavailable)",
        "method": "fallback",
    }

    model_overrides: dict[str, Any] = {"model": resolved_model}
    if temperature is not None:
        model_overrides["temperature"] = temperature

    results = await batched_judge(
        valid,
        build_prompt=build_prompt,
        parse=parse,
        fail_closed=fail_closed,
        system=system,
        batch_size=bs,
        model_overrides=model_overrides,
        deadline=deadline,
        stats=stats,
    )
    out: dict[str, "TutorialBuildabilityResult"] = {}
    for it, res in zip(valid, results):
        vid = str(it.get("video_id") or "").strip()
        if vid:
            out[vid] = res.value
    return out
