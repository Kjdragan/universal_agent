"""CSI Intelligence Pass — LLM-driven analysis of CSI packet actions.

Replaces the regex-based ``_memex_candidates_for_action`` extractor in
``services/claude_code_intel_replay.py`` with a single rich LLM call per
action that produces a structured ``VaultDelta``.

The LLM receives:
  - the tweet's text
  - the classifier's tier + reasoning (already-cached intelligence)
  - the fetched linked sources (Anthropic docs / blog posts / GitHub)
  - the list of slugs already in the vault (for CREATE-vs-EXTEND choice)

The LLM emits a structured ``VaultDelta`` describing exactly which vault
files should be created, extended, or revised. The persistence helper
(separate module, Phase E) translates each ``VaultAction`` into a call to
``wiki/core.py:memex_apply_action``. **Code never decides what is meaningful;
the LLM does.**

Architecture spec:
  docs/proactive_signals/knowledge_extraction_redesign_context_2026-05-07.md

Phase plan:
  docs/proactive_signals/csi_intelligence_pass_implementation_plan_2026-05-07.md

Hard rules:
  - Model: GLM-5.1 via ``resolve_opus()``. GLM-5.1 has no thinking mode;
    do NOT pass ``thinking={...}`` or ``reasoning_effort``.
  - Quality comes from prompt + context + structured output via tool_use,
    NOT from a reasoning parameter.
  - Code never makes meaning decisions. Stopword filters / URL-fragment
    detection / "is this a real entity?" judgment all live in the LLM.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from universal_agent.utils.model_resolution import resolve_opus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic schema — the structured LLM output
# ---------------------------------------------------------------------------


class VaultAction(BaseModel):
    """A single CREATE / EXTEND / REVISE operation on the vault."""

    op: Literal["create", "extend", "revise"] = Field(
        ..., description="What to do with this entity in the vault."
    )
    kind: Literal["product", "feature", "concept", "person", "event"] = Field(
        ..., description="Taxonomy tag for the entity."
    )
    name: str = Field(
        ...,
        description=(
            "Canonical, human-readable name. Multi-word names are preserved "
            "as multi-word ('Claude Managed Agents', not 'agents'). Do NOT "
            "emit URL fragments, t.co slugs, English stopwords, or joke "
            "words. If the action would be 'extend' or 'revise', this name "
            "should match (or canonicalize to) the existing entity."
        ),
    )
    aliases: list[str] = Field(
        default_factory=list,
        description=(
            "Other forms this entity is referenced by in the corpus. Used "
            "for future canonicalization. Example for 'Claude Opus 4.7': "
            "['Opus 4.7', 'opus-4.7', 'claude-opus-4-7']."
        ),
    )
    summary: str = Field(
        ...,
        description=(
            "1-3 sentences describing what this entity is. Written for a "
            "developer reader who isn't familiar with the entity yet."
        ),
    )
    key_facts: list[str] = Field(
        default_factory=list,
        description=(
            "Specific facts about this entity worth capturing in the vault. "
            "Use when the source content gives concrete details (rate limits, "
            "supported parameters, capabilities, integrations). Empty list "
            "if the source is just an announcement with no specifics."
        ),
    )
    source_post_ids: list[str] = Field(
        default_factory=list,
        description="X post IDs that contributed evidence for this action.",
    )
    source_doc_urls: list[str] = Field(
        default_factory=list,
        description=(
            "URLs of Anthropic docs / blog posts / GitHub repos that "
            "contributed evidence. Use the canonical resolved URL "
            "(platform.claude.com/docs/...), not the t.co shortlink."
        ),
    )
    confidence: Literal["high", "medium", "low"] = Field(
        ...,
        description=(
            "Your confidence that this entity is real and the summary is "
            "accurate. high = backed by linked Anthropic docs. medium = "
            "tweet-only evidence. low = inferred / speculative."
        ),
    )
    existing_slug: Optional[str] = Field(
        None,
        description=(
            "Required for op='extend' or op='revise'. The exact slug of "
            "the existing vault entity to extend/revise (e.g. 'mcp', "
            "'claude-managed-agents'). Pick from the list of existing "
            "entity slugs provided in the user message."
        ),
    )


class VaultRelation(BaseModel):
    """A typed edge between two vault entities."""

    from_slug: str = Field(..., description="Slug of the source entity.")
    to_slug: str = Field(..., description="Slug of the target entity.")
    kind: Literal[
        "uses", "feature-of", "alternative-to", "successor-to", "operates-on"
    ] = Field(..., description="Relation type.")


class VaultDelta(BaseModel):
    """The complete structured analysis output for a single action."""

    vault_actions: list[VaultAction] = Field(
        default_factory=list,
        description=(
            "List of CREATE/EXTEND/REVISE operations to apply. EMPTY list "
            "is valid and expected when the post contains nothing entity-"
            "worthy (e.g. 'Live now at https://t.co/EKyctqSCXB' — no "
            "vault_actions). Never emit a VaultAction just to have one."
        ),
    )
    relations: list[VaultRelation] = Field(
        default_factory=list,
        description=(
            "Typed edges between entities mentioned in this post. Empty "
            "list is fine when no clear relations are stated."
        ),
    )
    post_summary: str = Field(
        default="",
        description=(
            "1-2 sentence summary of the post itself, suitable for "
            "logging. Not for vault content."
        ),
    )
    post_tags: list[str] = Field(
        default_factory=list,
        description=(
            "Optional tags describing the post (e.g. 'release_announcement', "
            "'demo', 'commentary', 'event'). Used downstream for filtering."
        ),
    )


# ---------------------------------------------------------------------------
# System prompt — first draft, will be iterated in Phase C
# ---------------------------------------------------------------------------


_CSI_SYSTEM_PROMPT = """\
You are a senior analyst building a knowledge vault about Claude (Anthropic's \
LLM family), Claude Code, the Claude Agent SDK, MCP, and the broader \
agent-development ecosystem. Your job: read one social media post (plus its \
classifier rationale and any fetched source documents) and decide which \
real, durable entities deserve a page in the vault.

# Domain glossary you should know

- **Products:** Claude Code, Claude Code Web, Claude Code Mobile, Claude API, \
  Claude Managed Agents, Claude Agent SDK, MCP (Model Context Protocol), \
  Claude.ai, Anthropic Console, claude.com platform.
- **Models:** Opus 4.7 / Opus 4.6, Sonnet 4.6 / Sonnet 4.5, Haiku 4.5. The \
  family is "Claude" with versioned tiers.
- **SDK / tooling:** Agent SDK (Python + TypeScript), MCP Servers, the \
  `claude` CLI, Claude Code IDE extensions.
- **Concepts:** prompt caching, tool use, structured output, extended \
  thinking, computer use, batch API, files API, rate limits, context \
  windows, multiagent orchestration, outcomes loop, dreaming (in managed \
  agents), webhooks, sub-agents.
- **People (X handles):** @AnthropicAI, @ClaudeDevs (the dev-relations \
  account), @bcherny, @jared_kaplan, named developers and creators \
  who appear in announcements.

# Taxonomy

Each VaultAction must specify a `kind`:

- **product** — a thing Anthropic ships or hosts (Claude Code, Claude \
  Managed Agents, MCP).
- **feature** — a capability of a product (rate limits, prompt caching, \
  outcomes loop, webhook subscriptions). Features have a parent product; \
  emit a `relation` of kind `feature-of` linking them when the post makes \
  the parent clear.
- **concept** — an abstract pattern or technique (multi-agent \
  orchestration, retrieval augmented generation, context engineering). \
  Concepts can be product-agnostic.
- **person** — a named individual or X handle (rlancemartin, hackingdave). \
  Use the canonical handle (without `@`) as the name.
- **event** — a launch, hackathon, conference (Code with Claude 2026, \
  Code Day Berlin).

# What you MUST extract

Real entities described or referenced in the post. Multi-word names \
preserved as multi-word. Use the canonical capitalized form (e.g. "Claude \
Managed Agents", not "claude managed agents" or "managed agents"). When \
the post links to or describes Anthropic documentation, use that as \
evidence (`source_doc_urls`) and bump confidence to `high`.

# What you MUST NOT extract

- **t.co URL slugs** — anything that came from inside `https://t.co/<slug>`. \
  These are URL fragments, NOT entity names. Examples to refuse: \
  `EKyctqSCXB`, `gw9d0wedni`, `irghzxmkya`, `lWtgf4cDka`, `JEogw5vWly`. \
  Any random-looking 8-12 character alphanumeric token from a URL.
- **English stopwords** treated as entities — `the`, `and`, `for`, `all`, \
  `our`, `here`, `also`, `just`, `our`, `over`, `same`, `then`, `there`, \
  `would`, `could`, `should`, `start`, `starting`, `live`, `tell`, `read`, \
  `use`, `let`, `run`, `try`, `see`, `team`, `thanks`, `thank`, `enjoy`, \
  `appreciate`, `happy`, `fair`, `older`, `full`, `max`, `new`. NEVER \
  emit a VaultAction with one of these as `name`.
- **Joke or nonsense words** the post itself was riffing on (e.g. \
  "Flibbertigibetting" was a meme tweet, not a real product). If the \
  classifier reasoning calls something digest/whimsical/playful, the \
  capitalized words in it are noise.
- **Generic adjectives or verbs** as standalone entities — \
  `improving`, `building`, `working`, `cowork`, `pair` (when used as a \
  verb), `check`, `findings`, `fix`, `follow`, `hear`, `improving`, \
  `join`, `learn`, `live`, `memories`, `older`, `our`, `over`, \
  `separately`, `speed`, `start`, `starting`, `sunday`, `tuesday`. \
  Day-of-week names are not entities.
- **Code identifiers** taken verbatim from snippets (function names, \
  variables) unless the post is about that specific identifier as a \
  product feature.

# CREATE vs EXTEND vs REVISE

- The user message will include a list of slugs already in the vault. \
  Read it. If the entity you want to write about is already in the vault, \
  use `op="extend"` with `existing_slug=<that slug>` and put new facts \
  in `key_facts`. Don't CREATE a duplicate.
- Use `op="create"` only when the entity is genuinely new to the vault.
- Use `op="revise"` rarely — only when a post explicitly contradicts or \
  supersedes a previous claim about an existing entity (e.g. "X is now \
  deprecated, use Y instead"). REVISE writes a `_history/` snapshot of \
  the prior version.

# Empty output is valid and expected

Many posts contain nothing entity-worthy: greetings, retweets, "live now \
at <link>" pointers, jokes, conference banter. For those posts, emit \
`vault_actions: []` and `relations: []` and a brief `post_summary` \
(or empty). DO NOT emit fake VaultActions to avoid an empty list.

# Output format

Call the `emit_vault_delta` tool with a JSON argument matching the \
VaultDelta schema. Don't return prose; the tool call IS your output.\
"""


# ---------------------------------------------------------------------------
# Tool schema for structured output (tool_use API)
# ---------------------------------------------------------------------------


def _build_vault_delta_tool() -> dict[str, Any]:
    """Build the tool spec the LLM uses to emit structured VaultDelta JSON.

    Pydantic provides ``model_json_schema()``; we wrap that in the
    Anthropic-compatible tool envelope (``name``, ``description``,
    ``input_schema``).
    """
    return {
        "name": "emit_vault_delta",
        "description": (
            "Emit the structured analysis of this post — the list of "
            "vault entities to create/extend/revise, plus relations, "
            "summary, and tags. Empty vault_actions list is valid."
        ),
        "input_schema": VaultDelta.model_json_schema(),
    }


# ---------------------------------------------------------------------------
# LLM call (mirrors csi_url_judge._call_llm_structured pattern)
# ---------------------------------------------------------------------------


def _call_llm_structured(
    *,
    system: str,
    user: str,
    tool: dict[str, Any],
    max_retries: int = 2,
    max_output_tokens: int = 4096,
) -> dict[str, Any]:
    """Call GLM-5.1 via the Anthropic SDK with tool_use for structured output.

    Returns the tool's input dict (the VaultDelta JSON the model emitted).
    Mirrors ``csi_url_judge._call_llm_structured`` but allows a larger
    output token budget since vault deltas can be substantial.
    """
    from anthropic import Anthropic

    api_key = (
        os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or os.getenv("ZAI_API_KEY")
    )
    if not api_key:
        raise RuntimeError(
            "No Anthropic-compatible API key available "
            "(checked ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ZAI_API_KEY)"
        )

    client_kwargs: dict[str, Any] = {"api_key": api_key}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        client_kwargs["base_url"] = base_url

    client = Anthropic(**client_kwargs)

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=resolve_opus(),  # → glm-5.1 via ZAI map
                max_tokens=max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
            )
            for block in response.content:
                if hasattr(block, "type") and block.type == "tool_use":
                    return dict(block.input)  # type: ignore[arg-type]

            logger.warning(
                "CSI intelligence pass attempt %d: no tool_use block in response",
                attempt + 1,
            )
        except Exception as exc:  # pragma: no cover — network / SDK errors
            last_exc = exc
            logger.warning(
                "CSI intelligence pass attempt %d failed: %s", attempt + 1, exc
            )
            if attempt == max_retries - 1:
                raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(
        f"CSI intelligence pass returned no tool_use block after {max_retries} attempts"
    )


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _truncate(text: str, limit: int) -> str:
    """Simple character-count truncation with marker."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... truncated, original length {len(text)} chars]"


def _build_user_message(
    action: dict[str, Any],
    linked_sources: list[str],
    existing_vault_entities: list[str],
    *,
    max_chars_per_source: int = 8000,
    max_existing_entities_listed: int = 200,
) -> str:
    """Format one analysis call's user message.

    The message gives the LLM:
      - The post (text + metadata)
      - The classifier's reasoning (already-cached analysis)
      - Each fetched linked source (truncated)
      - The list of existing vault entity slugs (so CREATE-vs-EXTEND works)
    """
    parts: list[str] = []

    post_id = str(action.get("post_id") or "").strip()
    handle = str(action.get("handle") or action.get("user", "")).strip()
    text = str(action.get("text") or "").strip()
    tier = action.get("tier")
    classifier = action.get("classifier") if isinstance(action.get("classifier"), dict) else {}
    cls_action_type = str((classifier or {}).get("action_type") or "").strip()
    cls_reasoning = str((classifier or {}).get("reasoning") or "").strip()
    url = str(action.get("url") or "").strip()
    if not url and post_id and handle:
        url = f"https://x.com/{handle}/status/{post_id}"

    parts.append("# The post to analyze")
    parts.append("")
    if handle:
        parts.append(f"- Handle: @{handle}")
    if post_id:
        parts.append(f"- Post ID: {post_id}")
    if url:
        parts.append(f"- URL: {url}")
    if tier is not None:
        parts.append(f"- Classifier tier: {tier}")
    if cls_action_type:
        parts.append(f"- Classifier action_type: {cls_action_type}")
    parts.append("")
    parts.append("## Post text")
    parts.append("")
    parts.append(text or "(empty)")
    parts.append("")

    if cls_reasoning:
        parts.append("## Classifier reasoning")
        parts.append("")
        parts.append(_truncate(cls_reasoning, 2000))
        parts.append("")

    if linked_sources:
        parts.append(f"## Linked sources ({len(linked_sources)} fetched)")
        parts.append("")
        for i, src in enumerate(linked_sources, start=1):
            parts.append(f"### Source {i}")
            parts.append("")
            parts.append(_truncate(src, max_chars_per_source))
            parts.append("")
    else:
        parts.append("## Linked sources")
        parts.append("")
        parts.append("(No linked sources were fetched for this post.)")
        parts.append("")

    parts.append(
        f"# Existing vault entities ({len(existing_vault_entities)} total)"
    )
    parts.append("")
    parts.append(
        "When you decide to EXTEND or REVISE an entity, the `existing_slug` "
        "you emit MUST be one of these. If your candidate entity is "
        "already here under any reasonable name match, use EXTEND, not "
        "CREATE."
    )
    parts.append("")
    if existing_vault_entities:
        # Limit the listed entity count to keep the prompt size bounded.
        # Sort alphabetically for stable ordering.
        sorted_entities = sorted(existing_vault_entities)
        listed = sorted_entities[:max_existing_entities_listed]
        parts.append(", ".join(f"`{slug}`" for slug in listed))
        if len(sorted_entities) > max_existing_entities_listed:
            parts.append("")
            parts.append(
                f"... and {len(sorted_entities) - max_existing_entities_listed} more "
                f"(omitted to keep prompt size bounded)."
            )
    else:
        parts.append("(Vault is empty — every action will be op='create'.)")
    parts.append("")

    parts.append("# Now emit the VaultDelta")
    parts.append("")
    parts.append(
        "Call the `emit_vault_delta` tool with the structured analysis. "
        "Empty `vault_actions: []` is valid for posts with no entity-worthy "
        "content."
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def analyze_action(
    action: dict[str, Any],
    linked_sources: list[str],
    existing_vault_entities: list[str],
) -> VaultDelta:
    """Run one CSI intelligence-pass LLM call against a single action.

    Args:
        action: A single action dict from a packet's ``actions.json``. Should
            include at minimum ``post_id``, ``text``, and ``tier``. May
            include ``classifier`` (dict with ``reasoning`` + ``action_type``),
            ``handle``, ``url``.
        linked_sources: Body text of fetched linked sources (Anthropic docs,
            blog posts, GitHub repos). Each entry is a markdown/text string.
            Pass ``[]`` if no sources were fetched.
        existing_vault_entities: List of entity slugs already in the vault
            (e.g. ``["claude-code", "mcp", "managed-agents"]``). Used by the
            LLM to choose CREATE vs EXTEND. Pass ``[]`` for an empty vault.

    Returns:
        A validated ``VaultDelta``. Empty ``vault_actions`` is valid output.

    Raises:
        ``RuntimeError`` if no Anthropic-compatible API key is configured.
        ``pydantic.ValidationError`` if the LLM emits malformed JSON.
        SDK exceptions on network / model errors after exhausting retries.
    """
    user_msg = _build_user_message(
        action=action,
        linked_sources=linked_sources,
        existing_vault_entities=existing_vault_entities,
    )
    tool = _build_vault_delta_tool()
    raw = _call_llm_structured(
        system=_CSI_SYSTEM_PROMPT,
        user=user_msg,
        tool=tool,
    )
    return VaultDelta.model_validate(raw)


__all__ = [
    "VaultAction",
    "VaultRelation",
    "VaultDelta",
    "analyze_action",
]
