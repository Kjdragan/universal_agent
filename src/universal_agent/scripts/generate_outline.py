import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

from anthropic import AsyncAnthropic

from universal_agent.rate_limiter import ZAIRateLimiter

# Configuration
API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
MODEL = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-4.7")

def extract_json_payload(text: str) -> Dict[str, Any]:
    """Extract JSON from potential markdown wrapping"""
    text = text.strip()
    # Remove markdown code blocks if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Find start and end of code block
        if lines[0].startswith("```"):
            start_idx = 1
            if lines[0].strip() == "```json":
                 start_idx = 1
            
            # Find closing fence
            end_idx = len(lines)
            for i in range(start_idx, len(lines)):
                if lines[i].strip() == "```":
                    end_idx = i
                    break
            
            text = "\n".join(lines[start_idx:end_idx]).strip()

    # Find first { and last }
    start = text.find("{")
    end = text.rfind("}")
    
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in response")
        
    payload = text[start : end + 1]
    return json.loads(payload)

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
        # Fallback to research_overview.md if refined corpus missing (e.g. not generated yet)
        # But optimize_research_pipeline should ensure it exists.
        return f"Error: refined_corpus.md not found at {corpus_path}. Did you run finalize_research?"

    corpus_content = corpus_path.read_text(encoding="utf-8")
    
    # Truncate corpus if too massive for prompt (safety limit)
    # GLM-4 is 128k, let's keep it under 50k chars to be safe/fast
    if len(corpus_content) > 100000:
        corpus_content = corpus_content[:100000] + "\n...[TRUNCATED]..."

    prompt = f"""You are an expert research analyst. Create a comprehensive report outline based on the provided research corpus.

TOPIC: {topic}

CORPUS:
{corpus_content}

INSTRUCTIONS:
1. Create a logical structure for a detailed research report.
2. The report must include an "Executive Summary" as the first section.
3. Include 3-6 other substantive sections based on the corpus data.
4. Each section should have a clear, descriptive title and a brief description of what it will cover.
5. Identify specific filenames for each section (e.g., "01_executive_summary.md", "02_background.md").

OUTPUT FORMAT (JSON ONLY):
{{
  "title": "Report Title",
  "sections": [
    {{
      "id": "01_executive_summary",
      "title": "Executive Summary",
      "description": "Synthesize key findings...",
      "filename": "01_executive_summary.md"
    }},
    {{
      "id": "02_section_slug",
      "title": "Section Title",
      "description": "Covering...",
      "filename": "02_section_slug.md"
    }}
  ]
}}
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
        data = extract_json_payload(response_text)
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
