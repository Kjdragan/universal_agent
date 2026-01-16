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

async def write_section(sem, client, section, corpus_text):
    async with sem:
        out_path = Path(f"work_products/_working/sections/{section['id']}.md")
        if out_path.exists():
            print(f"Skipping {section['id']} (Exists)")
            return

        print(f"Drafting {section['title']}...")
        
        # Construct Prompt
        prompt = f"""You are a professional report writer.
        
        SECTION TITLE: {section['title']}
        CONTEXT:
        {corpus_text[:20000]} 
        
        INSTRUCTION: Write a detailed, fact-based section for this report. Use markdown."""

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
    # 1. Locate Resources
    outline_path = Path("work_products/_working/outline.json")
    if not outline_path.exists():
        print("Error: outline.json not found")
        return

    # 2. Locate Corpus (Try passed arg or specific task path)
    if len(sys.argv) > 1:
        corpus_path = Path(sys.argv[1])
    else:
        # Fallback: finding refined_corpus in tasks dir
        corpus_path = next(Path("tasks").glob("*/refined_corpus.md"), None)
    
    if not corpus_path or not corpus_path.exists():
        print("Error: refined_corpus.md not found")
        return

    print(f"Using Corpus: {corpus_path}")
    corpus_text = corpus_path.read_text()

    with open(outline_path) as f:
        data = json.load(f)
        sections = data.get("sections", [])

    # 3. Initialize Client
    client = AsyncAnthropic(api_key=API_KEY, base_url=BASE_URL)
    sem = asyncio.Semaphore(5) # Max 5 concurrent requests

    # 4. Run Parallel
    tasks = [write_section(sem, client, s, corpus_text) for s in sections]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    if not API_KEY:
        print("Error: ANTHROPIC_AUTH_TOKEN not set")
        sys.exit(1)
    asyncio.run(main())
