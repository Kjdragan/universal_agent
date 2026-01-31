import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field

from anthropic import AsyncAnthropic

from universal_agent.rate_limiter import ZAIRateLimiter
from universal_agent.utils.json_utils import extract_json_payload

# Configuration
API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
MODEL = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-4.7")

class OutlineSection(BaseModel):
    id: str = Field(..., description="Unique slug like '01_executive_summary'")
    title: str = Field(..., description="Human readable title")
    description: str = Field(..., description="Detailed instructions for writing this section")
    filename: str = Field(..., description="Target markdown filename (e.g., '01_executive_summary.md')")

class ReportOutline(BaseModel):
    title: str = Field(..., description="Clear Report Title")
    sections: List[OutlineSection]

async def generate_outline_async(workspace_path: Path, task_name: str, topic: str) -> str:
    """
    Generate an outline.json from the refined corpus.
    """
    if not API_KEY:
        return "Error: ANTHROPIC_AUTH_TOKEN/ZAI_API_KEY not set"

    if not workspace_path.exists():
        return f"Error: Workspace not found at {workspace_path}"

    # Locate refined corpus
    task_dir = workspace_path / "tasks" / task_name
    corpus_path = task_dir / "refined_corpus.md"
    
    if not corpus_path.exists():
        return f"Error: refined_corpus.md not found at {corpus_path}. Did you run finalize_research?"

    corpus_content = corpus_path.read_text(encoding="utf-8")
    
    # Truncate corpus if too massive for prompt (safety limit)
    if len(corpus_content) > 100000:
        corpus_content = corpus_content[:100000] + "\n...[TRUNCATED]..."

    prompt = f"""You are a professional research analyst. Your task is to generate a structured JSON outline for a research report based ON THE PROVIDED CORPUS.

TOPIC: {topic}

CORPUS:
{corpus_content}

Strict Instructions:
1. Logic: Group the corpus facts into 4-7 logical, high-impact sections.
2. Structure: The FIRST section must be a slug '01_executive_summary' with title 'Executive Summary'.
3. Detail: For each section, provide a concise 'description' summarizing what MUST be covered based on the corpus.
4. Format: You MUST return ONLY a valid JSON object. No explanation, no markdown text outside the block, no HTML.
5. Reliability: Ensure internal consistency in section IDs and filenames (format: XX_slug.md).

SCHEMA:
{{
  "title": "Clear Report Title",
  "sections": [
    {{
      "id": "01_executive_summary",
      "title": "Executive Summary",
      "description": "...",
      "filename": "01_executive_summary.md"
    }},
    ...
  ]
}}

RESPONSE:
"""

    # Use centralized rate limiter with SDK retries disabled
    client = AsyncAnthropic(api_key=API_KEY, base_url=BASE_URL, max_retries=0)
    limiter = ZAIRateLimiter.get_instance()
    
    MAX_RETRIES = 5
    resp = None
    last_error = None
    context = "generate_outline"
    
    for attempt in range(MAX_RETRIES):
        async with limiter.acquire(context):
            try:
                resp = await client.messages.create(
                    model=MODEL,
                    max_tokens=2000,
                    messages=[{"role": "user", "content": prompt}],
                )
                await limiter.record_success()
                break
            except Exception as e:
                error_str = str(e).lower()
                is_rate_limit = "429" in error_str or "too many requests" in error_str
                
                if is_rate_limit:
                    await limiter.record_429(context)
                    last_error = e
                    
                    if attempt < MAX_RETRIES - 1:
                        delay = limiter.get_backoff(attempt)
                        print(f"  ⚠️ [429] Rate limited ({context}). Backoff: {delay:.1f}s (Attempt {attempt+1}/{MAX_RETRIES})")
                        await asyncio.sleep(delay)
                        continue
                else:
                    return f"Error calling LLM: {e}"
    
    if not resp:
        return f"Error: Outline generation failed after max retries. Last error: {last_error}"

    response_text = resp.content[0].text
    
    try:
        # Layered recovery and Pydantic validation
        outline_obj = extract_json_payload(response_text, model=ReportOutline)
        data = outline_obj.model_dump()
    except Exception as exc:
        return f"Error parsing JSON from LLM: {exc}\nRaw output: {response_text[:200]}..."

    # Save to work_products/_working/outline.json
    working_dir = workspace_path / "work_products" / "_working"
    working_dir.mkdir(parents=True, exist_ok=True)
    
    outline_path = working_dir / "outline.json"
    outline_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    
    return f"✅ Outline generated successfully at {outline_path}\nSections: {len(data.get('sections', []))}"

async def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace", help="Path to session workspace")
    parser.add_argument("task_name", help="Task name")
    parser.add_argument("topic", help="Research topic")
    
    args = parser.parse_args()
    
    result = await generate_outline_async(Path(args.workspace), args.task_name, args.topic)
    print(result)
    return 0

if __name__ == "__main__":
    if len(sys.argv) < 4:
        # Fallback for direct testing or no args
        print("Usage: python generate_outline.py <workspace> <task_name> <topic>")
        sys.exit(1)
    
    sys.exit(asyncio.run(main()))
