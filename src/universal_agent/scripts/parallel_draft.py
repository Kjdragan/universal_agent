import os
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional
from anthropic import AsyncAnthropic

from universal_agent.rate_limiter import ZAIRateLimiter

# CONFIG - Auto-detect environment
API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
MODEL = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-5")

async def write_section(limiter: ZAIRateLimiter, client, section, corpus_text, order, base_path: Path, outline_meta: dict = None):
    """Write a single section using centralized rate limiter."""
    # Prefix with order number for correct assembly order
    output_dir = base_path / "work_products" / "_working" / "sections"
    out_path = output_dir / f"{order:02d}_{section['id']}.md"

    if out_path.exists():
        print(f"Skipping {section['id']} (Exists)")
        return

    # Identification
    title = section.get("title", "").strip()
    section_id = section.get("id", "").strip().lower()
    is_executive = section_id == "executive_summary" or "executive summary" in title.lower()

    # Ensure directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    if is_executive:
        print(f"Skipping generation for {section['id']} (Will be synthesized in cleanup phase)")
        out_path.write_text("# Executive Summary\n\n[Pending Synthesis by Cleanup Tool]", encoding="utf-8")
        return

    print(f"Drafting {section['title']}...")

    # Extract adaptive metadata from outline
    meta = outline_meta or {}
    tone = meta.get("tone", "analytical")
    material_type = meta.get("material_type", "analytical")
    comp = meta.get("component_guidance", {})
    narrative_role = section.get("narrative_role", "analysis")

    # Build adaptive writing instructions
    tone_guidance = {
        "journalistic": "Write in a journalistic voice — factual, attributed, timeline-driven. Lead with the most compelling fact or quote.",
        "analytical": "Write in an analytical voice — methodical, evidence-based, with clear reasoning. Lead with the key finding or framing question.",
        "narrative": "Write in a narrative voice — warm, engaging, with human details. Lead with a scene, anecdote, or compelling detail.",
        "investigative": "Write in an investigative voice — probing, evidence-driven, building toward revelations.",
        "explanatory": "Write in an explanatory voice — clear, pedagogical, breaking down complexity for an intelligent reader.",
        "authoritative": "Write in an authoritative voice — confident, precise, data-informed. Establish credibility through specificity.",
        "conversational": "Write in a conversational voice — approachable but substantive, as if explaining to a smart colleague.",
    }.get(tone, "Write in a professional, substantive voice.")

    # Component instructions based on outline guidance
    comp_instructions = []
    stat_level = comp.get("stat_cards", "moderate")
    if stat_level in ("heavy", "moderate"):
        comp_instructions.append(f"Highlight key statistics prominently (data density: {stat_level}).")
    elif stat_level == "none":
        comp_instructions.append("This topic is qualitative — do not force statistics where none are meaningful.")

    quote_level = comp.get("pull_quotes", "moderate")
    if quote_level in ("heavy", "moderate"):
        comp_instructions.append("Weave in direct quotes from sources when available.")
    elif quote_level == "none":
        comp_instructions.append("No attributable quotes expected — focus on analysis and synthesis.")

    comp_text = " ".join(comp_instructions) if comp_instructions else ""

    # Construct Prompt
    heading_line = f"## {title}"
    format_rules = "Start with the heading line exactly as shown. Use '###' for subheads as needed."

    prompt = f"""You are a professional report writer producing university-level content.

    REPORT CONTEXT:
    - Material type: {material_type}
    - This section's role: {narrative_role}

    TONE: {tone_guidance}

    REQUIRED HEADING (first line):
    {heading_line}

    SECTION TITLE: {title}
    SECTION DESCRIPTION: {section.get('description', '')}

    SOURCE MATERIAL:
    {corpus_text[:20000]}

    WRITING INSTRUCTIONS:
    Write a detailed, substantive section for this report. University-level quality — clear prose with logical flow and precise language. The reader is intelligent and engaged.

    {comp_text}

    Lead with substance — the most compelling fact, finding, or framing — not "This section discusses..." or "In this section, we will..."

    Use markdown only (no code fences). {format_rules} Focus on this section's topic and avoid repeating statistics central to other sections unless needed for context."""

    MAX_RETRIES = 5
    last_error = None
    context = section['id']
    
    for attempt in range(MAX_RETRIES):
        async with limiter.acquire(context):
            try:
                resp = await client.messages.create(
                    model=MODEL,
                    max_tokens=4000,
                    messages=[{"role": "user", "content": prompt}]
                )
                content = resp.content[0].text
                
                await limiter.record_success()
                out_path.write_text(content, encoding="utf-8")
                print(f"✓ Finished {section['id']}")
                return
            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "too many requests" in error_str or "high concurrency" in error_str
                
                is_timeout = "timed out" in error_str or "timeout" in error_str or "interrupted" in error_str or "connection" in error_str

                if is_rate_limit:
                    await limiter.record_429(context)
                    last_error = e
                    
                    if attempt < MAX_RETRIES - 1:
                        delay = limiter.get_backoff(attempt)
                        print(f"  ⚠️ [429] Rate limited ({context}). Backoff: {delay:.1f}s (Attempt {attempt+1}/{MAX_RETRIES})")
                        await asyncio.sleep(delay)
                        continue
                elif is_timeout:
                    last_error = e
                    if attempt < MAX_RETRIES - 1:
                        delay = min(10.0 * (attempt + 1), 30.0)
                        print(f"  ⚠️ [Timeout] {context} timed out. Retry in {delay:.0f}s (Attempt {attempt+1}/{MAX_RETRIES})")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        print(f"❌ Error {section['id']} (timeout after {MAX_RETRIES} attempts): {e}")
                        return
                else:
                    print(f"❌ Error {section['id']}: {e}")
                    return
    
    if last_error:
        print(f"❌ Max retries exhausted for {section['id']}: {last_error}")

async def draft_report_async(
    workspace_path: Path, 
    outline_path: Optional[Path] = None, 
    corpus_path: Optional[Path] = None
) -> str:
    """
    Async entry point for drafting reports.
    
    Args:
        workspace_path: Root directory of the session workspace.
        outline_path: Optional specific path to outline.json.
        corpus_path: Optional specific path to refined_corpus.md.
        
    Returns:
        Summary string of the operation.
    """
    if not workspace_path.exists():
        return f"Error: Workspace not found: {workspace_path}"

    # 1. Locate Resources
    if not outline_path:
        outline_path = workspace_path / "work_products" / "_working" / "outline.json"
    
    if not outline_path.exists():
        return f"Error: outline.json not found at {outline_path}"

    # 2. Locate Corpus
    if not corpus_path:
        # Try finding in tasks dir relative to workspace
        tasks_dir = workspace_path / "tasks"
        if tasks_dir.exists():
            corpus_path = next(tasks_dir.glob("*/refined_corpus.md"), None)
    
    if not corpus_path or not corpus_path.exists():
        return f"Error: refined_corpus.md not found in tasks/ directory of {workspace_path}"

    print(f"Using Corpus: {corpus_path}")
    corpus_text = corpus_path.read_text(encoding="utf-8")

    with open(outline_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        sections = data.get("sections", [])

    # Extract outline-level metadata for adaptive writing
    outline_meta = {
        "tone": data.get("tone", "analytical"),
        "material_type": data.get("material_type", "analytical"),
        "component_guidance": data.get("component_guidance", {}),
    }

    if not API_KEY:
        return "Error: ANTHROPIC_AUTH_TOKEN/ZAI_API_KEY not set"

    # 3. Initialize Client and Rate Limiter
    # Set max_retries=0 on SDK - we handle retries with the centralized rate limiter
    client = AsyncAnthropic(api_key=API_KEY, base_url=BASE_URL, max_retries=0, timeout=120.0)
    limiter = ZAIRateLimiter.get_instance()

    # 4. Run Parallel - rate limiter controls concurrency
    tasks = [write_section(limiter, client, s, corpus_text, i+1, workspace_path, outline_meta) for i, s in enumerate(sections)]
    await asyncio.gather(*tasks)
    
    return f"Drafting complete. Check {workspace_path}/work_products/_working/sections/"

async def main():
    # CLI Wrapper
    workspace = None
    if len(sys.argv) > 1:
        workspace = Path(sys.argv[1]).resolve()
    else:
        print("Error: No workspace path provided")
        return
    
    result = await draft_report_async(workspace)
    print(result)

if __name__ == "__main__":
    asyncio.run(main())
