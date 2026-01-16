import os
import asyncio
import json
import sys
from pathlib import Path
from anthropic import AsyncAnthropic

# CONFIG - Auto-detect environment
API_KEY = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
MODEL = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "glm-4.7")

async def write_section(sem, client, section, corpus_text, order):
    """Write a single section. order is used to prefix filename for correct ordering."""
    async with sem:
        # Prefix with order number for correct assembly order
        out_path = Path(f"work_products/_working/sections/{order:02d}_{section['id']}.md")
        if out_path.exists():
            print(f"Skipping {section['id']} (Exists)")
            return

        print(f"Drafting {section['title']}...")
        
        # Construct Prompt
        title = section.get("title", "").strip()
        section_id = section.get("id", "").strip().lower()
        is_executive = section_id == "executive_summary" or "executive summary" in title.lower()
        heading_line = "# Executive Summary" if is_executive else f"## {title}"
        format_rules = (
            "Provide 5-7 concise bullets. Avoid deep dives or repeating stats."
            if is_executive
            else "Start with the heading line exactly as shown. Use '###' for subheads as needed."
        )

        prompt = f"""You are a professional report writer.

        REQUIRED HEADING (first line):
        {heading_line}

        SECTION TITLE: {title}
        CONTEXT:
        {corpus_text[:20000]}

        INSTRUCTION: Write a detailed, fact-based section for this report. Use markdown only (no code fences). {format_rules} Focus on this section's topic and avoid repeating statistics central to other sections unless needed for context."""

        try:
            resp = await client.messages.create(
                model=MODEL,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )
            content = resp.content[0].text
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content)
            print(f"✓ Finished {section['id']}")
        except Exception as e:
            print(f"❌ Error {section['id']}: {e}")

async def main():
    # 0. Change to workspace directory if passed
    workspace = None
    if len(sys.argv) > 1:
        workspace = Path(sys.argv[1]).resolve()  # Use absolute path
        if workspace.exists() and workspace.is_dir():
            os.chdir(workspace)
            print(f"Working from: {workspace}")
        else:
            print(f"Error: Workspace not found or not a directory: {workspace}")
            return
    else:
        print("Error: No workspace path provided")
        return
    
    # 1. Locate Resources
    outline_path = Path("work_products/_working/outline.json")
    if not outline_path.exists():
        print(f"Error: outline.json not found at {outline_path.absolute()}")
        print(f"Current directory: {os.getcwd()}")
        return

    # 2. Locate Corpus (Try finding in tasks dir)
    corpus_path = next(Path("tasks").glob("*/refined_corpus.md"), None)
    
    if not corpus_path or not corpus_path.exists():
        print(f"Error: refined_corpus.md not found in tasks/")
        print(f"Current directory: {os.getcwd()}")
        print(f"Tasks directory exists: {Path('tasks').exists()}")
        if Path('tasks').exists():
            print(f"Tasks subdirectories: {list(Path('tasks').iterdir())}")
        return

    print(f"Using Corpus: {corpus_path}")
    corpus_text = corpus_path.read_text()

    with open(outline_path) as f:
        data = json.load(f)
        sections = data.get("sections", [])

    # 3. Initialize Client
    client = AsyncAnthropic(api_key=API_KEY, base_url=BASE_URL)
    sem = asyncio.Semaphore(5) # Max 5 concurrent requests

    # 4. Run Parallel - pass order index for filename ordering
    tasks = [write_section(sem, client, s, corpus_text, i+1) for i, s in enumerate(sections)]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if not API_KEY:
        print("Error: ANTHROPIC_AUTH_TOKEN not set")
        sys.exit(1)
    asyncio.run(main())
