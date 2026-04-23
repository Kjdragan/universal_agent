from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
from urllib.parse import urlparse

from universal_agent.services.claude_code_intel import (
    DEFAULT_HANDLE,
    _call_sync_llm,
    _has_llm_key,
    _parse_json_object,
    resolve_lane_root,
)


ROLLING_WINDOW_DAYS = 14
MAX_ACTION_CONTEXTS = 18

_ROLLUP_SYSTEM = """\
You are synthesizing the newest Claude Code / Claude Agent SDK developments into a builder-focused rolling intelligence brief.

Primary goal:
- extract practical leverage for building better agent systems and improving Universal Agent

Secondary goal:
- teach Kevin what changed and how to think about using it

Important:
- The tweet/post is discovery context only
- Official linked docs/repos/blogs are the canonical technical source when available
- Focus on reusable primitives, not vanity summaries
- Prefer concrete implementation leverage over academic explanation

Return ONLY valid JSON:
{
  "title": "string",
  "narrative_markdown": "markdown string with sections `## For Kevin` and `## For UA`",
  "bundles": [
    {
      "bundle_id": "short_slug",
      "title": "string",
      "summary": "string",
      "why_now": "string",
      "likely_ua_value": "short judgment",
      "likely_agent_system_value": "short judgment",
      "for_kevin_markdown": "markdown string",
      "for_ua_markdown": "markdown string",
      "recommended_variant": "variant_key",
      "canonical_sources": [
        {
          "title": "string",
          "url": "https://...",
          "source_type": "string",
          "domain": "string",
          "why_canonical": "string"
        }
      ],
      "discovery_posts": ["post_id"],
      "variants": [
        {
          "key": "variant_key",
          "label": "string",
          "intent": "string",
          "applicability": ["UA" | "Agent SDK" | "Shared"],
          "confidence": "low" | "medium" | "high",
          "primitives": [
            {
              "kind": "prompt_pattern" | "workflow_recipe" | "code_snippet" | "skill_skeleton" | "agent_pattern" | "integration_note" | "demo_implementation" | "ua_adaptation_pattern" | "agent_sdk_adaptation_pattern",
              "title": "string",
              "rationale": "string",
              "content_markdown": "markdown string"
            }
          ]
        }
      ]
    }
  ]
}
"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def capability_library_root() -> Path:
    return _repo_root() / "agent_capability_library" / "claude_code_intel"


def rolling_root(artifacts_root: Path | None = None) -> Path:
    return resolve_lane_root(artifacts_root) / "rolling"


def _rolling_current_dir(artifacts_root: Path | None = None) -> Path:
    return rolling_root(artifacts_root) / "current"


def _rolling_history_dir(artifacts_root: Path | None = None) -> Path:
    return rolling_root(artifacts_root) / "history"


def _artifact_api_url(path: Path, *, artifacts_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(artifacts_root.resolve()).as_posix()
    except Exception:
        return ""
    return f"/api/artifacts/files/{rel}"


def _safe_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists() or not path.is_file():
            return default
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _safe_markdown(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _slugify(value: str, *, fallback: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or fallback


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _priority_source_type(source_type: str) -> int:
    order = {
        "vendor_docs": 0,
        "docs_page": 1,
        "github_file": 2,
        "github_repo": 3,
        "github_tree": 4,
        "vendor_web": 5,
        "event_page": 6,
        "generic_web": 7,
        "non_html": 8,
        "x_page": 9,
    }
    return order.get(str(source_type or "").strip(), 10)


def _why_canonical(source_type: str, domain: str) -> str:
    source_type = str(source_type or "").strip()
    domain = str(domain or "").strip()
    if source_type in {"vendor_docs", "docs_page"}:
        return "official documentation with direct implementation guidance"
    if source_type.startswith("github"):
        return "repository-level implementation material and concrete code context"
    if "claude.com" in domain or "anthropic.com" in domain:
        return "official Anthropic / Claude source material"
    return "best available linked technical reference for the capability"


def _load_recent_action_contexts(*, artifacts_root: Path | None = None, window_days: int = ROLLING_WINDOW_DAYS) -> list[dict[str, Any]]:
    packet_root = resolve_lane_root(artifacts_root) / "packets"
    if not packet_root.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(window_days or ROLLING_WINDOW_DAYS)))
    contexts: list[dict[str, Any]] = []
    seen_posts: set[str] = set()

    packet_dirs = [
        candidate
        for date_dir in packet_root.iterdir()
        if date_dir.is_dir()
        for candidate in date_dir.iterdir()
        if candidate.is_dir()
    ]
    packet_dirs.sort(key=lambda path: (path.parent.name, path.name), reverse=True)

    for packet_dir in packet_dirs:
        manifest = _safe_json(packet_dir / "manifest.json", {})
        generated_at = str(manifest.get("generated_at") or "")
        generated_dt = _parse_iso(generated_at)
        if generated_dt is None or generated_dt < cutoff:
            continue

        actions = _safe_json(packet_dir / "actions.json", [])
        linked_entries = _safe_json(packet_dir / "linked_sources.json", [])
        linked_by_post: dict[str, list[dict[str, Any]]] = {}
        for entry in linked_entries:
            if not isinstance(entry, dict):
                continue
            post_id = str(entry.get("post_id") or "").strip()
            if not post_id:
                continue
            metadata_path = Path(str(entry.get("metadata_path") or ""))
            metadata = _safe_json(metadata_path, {}) if metadata_path.exists() else {}
            final_url = str(metadata.get("final_url") or entry.get("url") or "").strip()
            domain = str(metadata.get("domain") or urlparse(final_url).netloc.lower())
            source_type = str(metadata.get("source_type") or "").strip()
            if str(entry.get("fetch_status") or "") != "fetched":
                continue
            if domain in {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "t.co"} or source_type == "x_page":
                continue
            linked_by_post.setdefault(post_id, []).append(
                {
                    "title": str(metadata.get("title") or entry.get("title") or final_url or "Linked source"),
                    "url": final_url,
                    "source_type": source_type or "generic_web",
                    "domain": domain,
                    "summary_excerpt": str(metadata.get("summary_excerpt") or "").strip(),
                    "why_canonical": _why_canonical(source_type=source_type, domain=domain),
                }
            )

        for action in actions:
            if not isinstance(action, dict):
                continue
            post_id = str(action.get("post_id") or "").strip()
            if not post_id or post_id in seen_posts:
                continue
            seen_posts.add(post_id)
            canonical_sources = sorted(
                linked_by_post.get(post_id, []),
                key=lambda item: (_priority_source_type(str(item.get("source_type") or "")), str(item.get("domain") or "")),
            )
            contexts.append(
                {
                    "post_id": post_id,
                    "generated_at": generated_at,
                    "packet_dir": str(packet_dir),
                    "packet_name": packet_dir.name,
                    "packet_day": packet_dir.parent.name,
                    "tier": int(action.get("tier") or 0),
                    "action_type": str(action.get("action_type") or ""),
                    "text": str(action.get("text") or "").strip(),
                    "post_url": str(action.get("url") or ""),
                    "links": [str(link) for link in (action.get("links") or []) if str(link).strip()],
                    "classifier_reasoning": str(((action.get("classifier") or {}) if isinstance(action.get("classifier"), dict) else {}).get("reasoning") or ""),
                    "canonical_sources": canonical_sources[:5],
                }
            )

    contexts.sort(key=lambda item: (str(item.get("generated_at") or ""), int(item.get("tier") or 0)), reverse=True)
    return contexts[:MAX_ACTION_CONTEXTS]


def _fallback_narrative(contexts: list[dict[str, Any]], *, window_days: int) -> str:
    if not contexts:
        return (
            f"# Rolling {window_days}-Day Claude Code Builder Brief\n\n"
            "## For Kevin\n\n"
            "No recent ClaudeDevs capability changes were available in the rolling window.\n\n"
            "## For UA\n\n"
            "No new capability bundles were synthesized in this window.\n"
        )

    source_domains = Counter(
        source.get("domain") or ""
        for item in contexts
        for source in (item.get("canonical_sources") or [])
        if str(source.get("domain") or "").strip()
    )
    top_domains = ", ".join(domain for domain, _ in source_domains.most_common(3)) or "linked docs and repos"
    top_items = contexts[:3]
    kevin_lines = [
        f"Over the last {window_days} days, the most meaningful Claude Code developments clustered around {top_domains}.",
        "The practical pattern is that new capability announcements quickly expand into more concrete usage guidance in linked official docs and demos.",
        "The current opportunity is to turn those updates into reusable agent-building primitives instead of leaving them as interesting release notes.",
    ]
    ua_lines = [
        "Treat the official linked docs and repos as the canonical implementation layer.",
        "Prefer building reusable prompt, workflow, code, and adaptation primitives instead of one-off experiments.",
        "Use the rolling capability bundles below as the first retrieval target when building new UA or Agent SDK functionality.",
    ]
    lines = [f"# Rolling {window_days}-Day Claude Code Builder Brief", "", "## For Kevin", ""]
    lines.extend(f"- {line}" for line in kevin_lines)
    lines.extend(["", "### Most Actionable Changes", ""])
    for item in top_items:
        lines.append(
            f"- `{item['action_type']}` / tier `{item['tier']}`: {item['text'][:220]}"
        )
    lines.extend(["", "## For UA", ""])
    lines.extend(f"- {line}" for line in ua_lines)
    return "\n".join(lines).rstrip() + "\n"


def _fallback_bundle(context: dict[str, Any]) -> dict[str, Any]:
    title_seed = ""
    sources = list(context.get("canonical_sources") or [])
    if sources:
        title_seed = str(sources[0].get("title") or "")
    if not title_seed:
        title_seed = str(context.get("text") or "")[:72]
    bundle_id = _slugify(title_seed, fallback=str(context.get("post_id") or "bundle"))
    source_titles = ", ".join(str(source.get("title") or source.get("url") or "") for source in sources[:3])
    ua_pattern = (
        "## UA Adaptation Pattern\n\n"
        f"- Anchor on the canonical source(s): {source_titles or 'linked technical references'}.\n"
        "- Distill the feature into reusable prompt/workflow/code assets before wiring it into runtime behavior.\n"
        "- Prefer adding this as a reusable building block for Simone/Cody and future client-system builds.\n"
    )
    agent_sdk_pattern = (
        "## Agent SDK Adaptation Pattern\n\n"
        "- Translate the capability into standalone agent-system patterns, not just IDE assistance.\n"
        "- Capture the control flow, prompt shape, and code surface required to reproduce the feature in a new project.\n"
        "- Preserve source provenance so future coding agents can rebuild from official references.\n"
    )
    shared_prompt = (
        "## Prompt Pattern\n\n"
        "```text\n"
        "Given the official feature docs and any linked demo material, synthesize:\n"
        "1. the minimal agent behavior contract,\n"
        "2. the reusable prompt skeleton,\n"
        "3. the workflow boundaries,\n"
        "4. the failure modes we should guard against.\n"
        "```\n"
    )
    # Build enriched for_kevin with linked source context
    kevin_sections = ["## What changed\n"]
    kevin_sections.append(context.get("text") or "A new capability or workflow detail was surfaced.")
    if sources:
        kevin_sections.append("\n## Canonical Sources\n")
        for source in sources[:3]:
            s_title = str(source.get("title") or source.get("url") or "Source")
            s_url = str(source.get("url") or "")
            s_excerpt = str(source.get("summary_excerpt") or "").strip()
            kevin_sections.append(f"- [{s_title}]({s_url})")
            if s_excerpt:
                kevin_sections.append(f"  - {s_excerpt[:200]}")
    kevin_sections.append("\n## Why it matters\n")
    kevin_sections.append(
        "This is most valuable if we can turn it into reusable building blocks quickly, "
        "before the underlying capability disappears into stale model knowledge."
    )
    for_kevin = "\n".join(kevin_sections) + "\n"
    # Build enriched for_ua with linked source context
    ua_lines = [
        "## UA Adoption Package\n",
    ]
    if sources:
        for source in sources[:3]:
            s_title = str(source.get("title") or source.get("url") or "Source")
            s_url = str(source.get("url") or "")
            ua_lines.append(f"- Canonical source: [{s_title}]({s_url})")
    ua_lines.extend([
        "- Materialize multiple implementation primitives, not just a narrative summary.",
        "- Keep the output reusable for UA and standalone Agent SDK projects.",
    ])
    for_ua = "\n".join(ua_lines) + "\n"
    return {
        "bundle_id": bundle_id,
        "title": title_seed or f"Capability bundle {context.get('post_id')}",
        "summary": str(context.get("text") or "")[:260],
        "why_now": "Recent ClaudeDevs updates suggest this capability is new enough that model cutoffs may miss it, so we should materialize it now.",
        "likely_ua_value": "Likely useful for extending UA workflows and agent building blocks.",
        "likely_agent_system_value": "Likely transferable into standalone agent-system projects.",
        "for_kevin_markdown": for_kevin,
        "for_ua_markdown": for_ua,
        "recommended_variant": "ua-adaptation",
        "canonical_sources": sources,
        "discovery_posts": [str(context.get("post_id") or "")],
        "variants": [
            {
                "key": "ua-adaptation",
                "label": "UA Adaptation",
                "intent": "Translate the capability into reusable UA building blocks.",
                "applicability": ["UA", "Shared"],
                "confidence": "medium",
                "primitives": [
                    {
                        "kind": "ua_adaptation_pattern",
                        "title": "UA adaptation pattern",
                        "rationale": "Turn the capability into a repeatable UA integration pattern.",
                        "content_markdown": ua_pattern,
                    },
                    {
                        "kind": "workflow_recipe",
                        "title": "Workflow recipe",
                        "rationale": "Capture the control flow needed to use the capability repeatedly.",
                        "content_markdown": (
                            "## Workflow Recipe\n\n"
                            "1. Read the canonical linked source.\n"
                            "2. Extract the behavior contract.\n"
                            "3. Generate prompt/code/workflow primitives.\n"
                            "4. Validate the most promising path in UA.\n"
                        ),
                    },
                ],
            },
            {
                "key": "agent-sdk-transfer",
                "label": "Agent SDK Transfer",
                "intent": "Translate the capability into standalone agent-system primitives.",
                "applicability": ["Agent SDK", "Shared"],
                "confidence": "medium",
                "primitives": [
                    {
                        "kind": "agent_sdk_adaptation_pattern",
                        "title": "Agent SDK adaptation pattern",
                        "rationale": "Frame the capability for standalone agent systems.",
                        "content_markdown": agent_sdk_pattern,
                    },
                    {
                        "kind": "prompt_pattern",
                        "title": "Prompt pattern",
                        "rationale": "Capture the reusable instruction shape behind the capability.",
                        "content_markdown": shared_prompt,
                    },
                ],
            },
        ],
    }


def _fallback_synthesis(contexts: list[dict[str, Any]], *, window_days: int) -> dict[str, Any]:
    bundles = [_fallback_bundle(context) for context in contexts[:6]]
    return {
        "title": f"Rolling {window_days}-Day Claude Code Builder Brief",
        "narrative_markdown": _fallback_narrative(contexts, window_days=window_days),
        "bundles": bundles,
        "synthesis_method": "fallback",
    }


def _llm_synthesis(contexts: list[dict[str, Any]], *, window_days: int) -> dict[str, Any]:
    if not contexts or not _has_llm_key():
        if not _has_llm_key() and contexts:
            logger.warning("Rolling synthesis using fallback: no LLM API key available")
        return _fallback_synthesis(contexts, window_days=window_days)

    prompt_payload = []
    for item in contexts:
        prompt_payload.append(
            {
                "post_id": item["post_id"],
                "generated_at": item["generated_at"],
                "tier": item["tier"],
                "action_type": item["action_type"],
                "text": item["text"][:700],
                "post_url": item["post_url"],
                "classifier_reasoning": item["classifier_reasoning"][:240],
                "canonical_sources": [
                    {
                        "title": str(source.get("title") or "")[:180],
                        "url": source.get("url") or "",
                        "source_type": source.get("source_type") or "",
                        "domain": source.get("domain") or "",
                        "summary_excerpt": str(source.get("summary_excerpt") or "")[:220],
                    }
                    for source in (item.get("canonical_sources") or [])[:4]
                ],
            }
        )

    user = (
        f"Synthesize a rolling {window_days}-day builder brief and capability bundles.\n\n"
        "Source contexts:\n"
        f"{json.dumps(prompt_payload, indent=2, ensure_ascii=True)}"
    )
    try:
        raw = _call_sync_llm(system=_ROLLUP_SYSTEM, user=user, max_tokens=7000)
        parsed = _parse_json_object(raw)
        bundles = parsed.get("bundles")
        if not isinstance(bundles, list):
            raise ValueError("bundles_missing")
        for bundle in bundles:
            if not isinstance(bundle, dict):
                raise ValueError("bundle_not_object")
            bundle["bundle_id"] = _slugify(str(bundle.get("bundle_id") or bundle.get("title") or ""), fallback="bundle")
        return {
            "title": str(parsed.get("title") or f"Rolling {window_days}-Day Claude Code Builder Brief"),
            "narrative_markdown": str(parsed.get("narrative_markdown") or "").strip() or _fallback_narrative(contexts, window_days=window_days),
            "bundles": bundles,
            "synthesis_method": "llm",
        }
    except Exception as exc:
        logger.warning("Rolling LLM synthesis failed; using fallback: %s: %s", type(exc).__name__, exc)
        return _fallback_synthesis(contexts, window_days=window_days)


def _bundle_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        f"# {bundle.get('title') or 'Capability Bundle'}",
        "",
        f"- Bundle ID: `{bundle.get('bundle_id') or ''}`",
        f"- Recommended variant: `{bundle.get('recommended_variant') or ''}`",
        f"- UA value: {bundle.get('likely_ua_value') or ''}",
        f"- Agent-system value: {bundle.get('likely_agent_system_value') or ''}",
        "",
        "## Summary",
        "",
        str(bundle.get("summary") or ""),
        "",
        "## Why Now",
        "",
        str(bundle.get("why_now") or ""),
        "",
        "## For Kevin",
        "",
        str(bundle.get("for_kevin_markdown") or ""),
        "",
        "## For UA",
        "",
        str(bundle.get("for_ua_markdown") or ""),
        "",
        "## Canonical Sources",
        "",
    ]
    for source in bundle.get("canonical_sources") or []:
        lines.append(
            f"- [{source.get('title') or source.get('url') or 'Source'}]({source.get('url') or ''}) "
            f"— `{source.get('source_type') or ''}` / `{source.get('domain') or ''}`"
        )
    lines.extend(["", "## Variants", ""])
    for variant in bundle.get("variants") or []:
        lines.extend(
            [
                f"### {variant.get('label') or variant.get('key') or 'Variant'}",
                "",
                f"- Key: `{variant.get('key') or ''}`",
                f"- Intent: {variant.get('intent') or ''}",
                f"- Applicability: `{json.dumps(variant.get('applicability') or [])}`",
                f"- Confidence: `{variant.get('confidence') or ''}`",
                "",
            ]
        )
        for primitive in variant.get("primitives") or []:
            lines.extend(
                [
                    f"#### {primitive.get('title') or primitive.get('kind') or 'Primitive'}",
                    "",
                    f"- Kind: `{primitive.get('kind') or ''}`",
                    f"- Rationale: {primitive.get('rationale') or ''}",
                    "",
                    str(primitive.get("content_markdown") or ""),
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _materialize_bundle_derivatives(bundle_dir: Path, bundle: dict[str, Any]) -> None:
    primitives_root = bundle_dir / "primitives"
    primitives_root.mkdir(parents=True, exist_ok=True)
    for variant in bundle.get("variants") or []:
        variant_key = _slugify(str(variant.get("key") or variant.get("label") or "variant"), fallback="variant")
        for index, primitive in enumerate(variant.get("primitives") or [], start=1):
            kind = _slugify(str(primitive.get("kind") or "primitive"), fallback="primitive")
            title = _slugify(str(primitive.get("title") or kind), fallback=kind)
            path = primitives_root / f"{variant_key}__{index:02d}__{kind}__{title}.md"
            body = (
                f"# {primitive.get('title') or kind}\n\n"
                f"- Variant: `{variant.get('key') or ''}`\n"
                f"- Kind: `{primitive.get('kind') or ''}`\n"
                f"- Rationale: {primitive.get('rationale') or ''}\n\n"
                f"{primitive.get('content_markdown') or ''}\n"
            )
            path.write_text(body, encoding="utf-8")


def _write_current_and_history(*, synthesis: dict[str, Any], artifacts_root: Path) -> dict[str, Any]:
    current_dir = _rolling_current_dir(artifacts_root)
    history_dir = _rolling_history_dir(artifacts_root)
    current_bundles_dir = current_dir / "bundles"
    history_stamp = _timestamp_slug()
    generated_at_iso = datetime.now(timezone.utc).isoformat()
    history_item_dir = history_dir / history_stamp

    for path in (current_dir, current_bundles_dir, history_item_dir):
        path.mkdir(parents=True, exist_ok=True)

    narrative_md_path = current_dir / "rolling_14_day_report.md"
    narrative_json_path = current_dir / "rolling_14_day_report.json"
    narrative_history_md = history_item_dir / "rolling_14_day_report.md"
    narrative_history_json = history_item_dir / "rolling_14_day_report.json"

    bundle_index = []
    for bundle in synthesis.get("bundles") or []:
        bundle_id = _slugify(str(bundle.get("bundle_id") or bundle.get("title") or ""), fallback="bundle")
        bundle["bundle_id"] = bundle_id
        bundle_dir = current_bundles_dir / bundle_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bundle_json_path = bundle_dir / "bundle.json"
        bundle_md_path = bundle_dir / "bundle.md"
        bundle_json_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
        bundle_md_path.write_text(_bundle_markdown(bundle), encoding="utf-8")
        bundle_index.append(
            {
                "bundle_id": bundle_id,
                "title": str(bundle.get("title") or bundle_id),
                "summary": str(bundle.get("summary") or ""),
                "json_path": str(bundle_json_path),
                "markdown_path": str(bundle_md_path),
            }
        )

    narrative_payload = {
        "generated_at": generated_at_iso,
        "window_days": ROLLING_WINDOW_DAYS,
        "title": str(synthesis.get("title") or f"Rolling {ROLLING_WINDOW_DAYS}-Day Claude Code Builder Brief"),
        "narrative_markdown": str(synthesis.get("narrative_markdown") or ""),
        "bundle_count": len(bundle_index),
        "bundles": synthesis.get("bundles") or [],
        "synthesis_method": str(synthesis.get("synthesis_method") or "unknown"),
    }
    narrative_md_path.write_text(str(synthesis.get("narrative_markdown") or "").rstrip() + "\n", encoding="utf-8")
    narrative_json_path.write_text(json.dumps(narrative_payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    narrative_history_md.write_text(narrative_md_path.read_text(encoding="utf-8"), encoding="utf-8")
    narrative_history_json.write_text(narrative_json_path.read_text(encoding="utf-8"), encoding="utf-8")
    return {
        "report_markdown_path": str(narrative_md_path),
        "report_json_path": str(narrative_json_path),
        "history_dir": str(history_item_dir),
        "bundles_dir": str(current_bundles_dir),
    }


def _materialize_repo_library(*, synthesis: dict[str, Any]) -> dict[str, Any]:
    library_root = capability_library_root()
    current_root = library_root / "current"
    bundles_root = current_root / "bundles"
    current_root.mkdir(parents=True, exist_ok=True)
    bundles_root.mkdir(parents=True, exist_ok=True)

    readme_path = library_root / "README.md"
    if not readme_path.exists():
        readme_path.write_text(
            "# Claude Code Intel Capability Library\n\n"
            "Machine-usable capability bundles synthesized from the ClaudeDevs X intelligence lane.\n",
            encoding="utf-8",
        )

    report_md_path = current_root / "rolling_14_day_report.md"
    report_json_path = current_root / "rolling_14_day_report.json"
    report_md_path.write_text(str(synthesis.get("narrative_markdown") or "").rstrip() + "\n", encoding="utf-8")
    report_json_path.write_text(json.dumps(synthesis, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")

    bundle_index = []
    for bundle in synthesis.get("bundles") or []:
        bundle_id = _slugify(str(bundle.get("bundle_id") or bundle.get("title") or ""), fallback="bundle")
        bundle["bundle_id"] = bundle_id
        bundle_dir = bundles_root / bundle_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bundle_json_path = bundle_dir / "bundle.json"
        bundle_md_path = bundle_dir / "bundle.md"
        bundle_json_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
        bundle_md_path.write_text(_bundle_markdown(bundle), encoding="utf-8")
        _materialize_bundle_derivatives(bundle_dir, bundle)
        bundle_index.append(
            {
                "bundle_id": bundle_id,
                "title": str(bundle.get("title") or bundle_id),
                "summary": str(bundle.get("summary") or ""),
                "recommended_variant": str(bundle.get("recommended_variant") or ""),
                "bundle_dir": str(bundle_dir),
            }
        )

    (current_root / "index.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "bundles": bundle_index}, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"root": str(library_root), "current_root": str(current_root), "bundle_count": len(bundle_index)}


def build_rolling_assets(*, artifacts_root: Path | None = None, window_days: int = ROLLING_WINDOW_DAYS) -> dict[str, Any]:
    lane_root = resolve_lane_root(artifacts_root)
    lane_root.mkdir(parents=True, exist_ok=True)
    contexts = _load_recent_action_contexts(artifacts_root=artifacts_root, window_days=window_days)
    synthesis = _llm_synthesis(contexts, window_days=window_days)
    artifact_outputs = _write_current_and_history(synthesis=synthesis, artifacts_root=lane_root.parent.parent)
    repo_outputs = _materialize_repo_library(synthesis=synthesis)
    current_json_path = Path(artifact_outputs["report_json_path"])
    current_payload = _safe_json(current_json_path, {})
    current_payload.update(
        {
            "artifact_outputs": artifact_outputs,
            "repo_outputs": repo_outputs,
            "source_action_count": len(contexts),
        }
    )
    current_json_path.write_text(json.dumps(current_payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n", encoding="utf-8")
    return current_payload
