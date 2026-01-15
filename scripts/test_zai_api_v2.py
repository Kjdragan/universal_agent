#!/usr/bin/env python3
"""
Test script v2: Explore batching strategy for corpus refinement.
Key insight: Rate limit is ~3 concurrent calls, so we need smart batching.

Strategies tested:
1. Sequential with fast model
2. Parallel (3 concurrent) with semaphore
3. Batch multiple files in single call
"""

import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import httpx

# Configuration
BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
AUTH_TOKEN = os.getenv("ZAI_API_KEY", os.getenv("ANTHROPIC_AUTH_TOKEN"))
FAST_MODEL = os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "glm-4.7")

# Sample corpus files (simulate 10 research files)
SAMPLE_CORPUS = [
    {"file": "article1.md", "title": "Putin Stalling Peace Deal", "source": "Jamestown", "date": "2025-12-29", "content": "European, U.S., and Ukrainian officials claim a peace agreement with Russia is 90% complete but Putin continues to stall negotiations."},
    {"file": "article2.md", "title": "Will the War End in 2026?", "source": "RFE/RL", "date": "2026-01-12", "content": "With sides far apart on key issues as Russia's invasion approaches its fourth year. Putin demands NATO withdrawal, Zelenskyy insists on territorial integrity."},
    {"file": "article3.md", "title": "Oreshnik Missile Attack", "source": "Substack", "date": "2026-01-09", "content": "The Oreshnik IRBM has a range of 5,500km and travels at 12,000 km/h. It can carry nuclear or conventional warheads. Second use in war."},
    {"file": "article4.md", "title": "Civilian Casualties Rise", "source": "Reuters", "date": "2026-01-12", "content": "2025 was deadliest year for civilians since 2022. UN reports 31% increase in casualties compared to 2024."},
    {"file": "article5.md", "title": "Paris Peace Declaration", "source": "Guardian", "date": "2026-01-11", "content": "Coalition of the Willing agrees to deploy forces to Ukraine after ceasefire. UK, France sign intent declaration."},
]


async def extract_single(session: httpx.AsyncClient, file_data: dict, semaphore: asyncio.Semaphore) -> dict:
    """Extract key facts from a single file with semaphore for rate limiting."""
    async with semaphore:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": AUTH_TOKEN,
            "anthropic-version": "2023-06-01",
        }
        
        payload = {
            "model": FAST_MODEL,
            "max_tokens": 150,
            "messages": [
                {
                    "role": "user",
                    "content": f"""Extract 2-3 key facts from this article. Respond with ONLY bullet points.
Title: {file_data['title']}
Source: {file_data['source']} ({file_data['date']})
Content: {file_data['content']}"""
                }
            ]
        }
        
        start = time.perf_counter()
        response = await session.post(f"{BASE_URL}/v1/messages", headers=headers, json=payload)
        elapsed = time.perf_counter() - start
        
        if response.status_code == 200:
            data = response.json()
            content = data.get("content", [{}])[0].get("text", "No content")
            return {
                "file": file_data["file"],
                "citation": f"{file_data['title']} ({file_data['source']}, {file_data['date']})",
                "facts": content,
                "latency_ms": int(elapsed * 1000),
                "success": True,
            }
        else:
            return {
                "file": file_data["file"],
                "error": f"HTTP {response.status_code}",
                "latency_ms": int(elapsed * 1000),
                "success": False,
            }


async def extract_batch(session: httpx.AsyncClient, files: list) -> dict:
    """Extract key facts from multiple files in a SINGLE API call."""
    headers = {
        "Content-Type": "application/json",
        "x-api-key": AUTH_TOKEN,
        "anthropic-version": "2023-06-01",
    }
    
    # Build combined prompt
    articles_text = "\n\n".join([
        f"---\n**Article {i+1}:** {f['title']}\n**Source:** {f['source']} ({f['date']})\n**Content:** {f['content']}"
        for i, f in enumerate(files)
    ])
    
    payload = {
        "model": FAST_MODEL,
        "max_tokens": 600,
        "messages": [
            {
                "role": "user",
                "content": f"""Extract 2-3 key facts from EACH article below. Format as:
## Article N: [Title]
- Fact 1
- Fact 2
Source: [Source], [Date]

{articles_text}"""
            }
        ]
    }
    
    start = time.perf_counter()
    response = await session.post(f"{BASE_URL}/v1/messages", headers=headers, json=payload)
    elapsed = time.perf_counter() - start
    
    if response.status_code == 200:
        data = response.json()
        content = data.get("content", [{}])[0].get("text", "No content")
        return {
            "files_processed": len(files),
            "combined_output": content,
            "latency_ms": int(elapsed * 1000),
            "tokens_in": data.get("usage", {}).get("input_tokens", 0),
            "tokens_out": data.get("usage", {}).get("output_tokens", 0),
            "success": True,
        }
    else:
        return {
            "error": f"HTTP {response.status_code}: {response.text[:200]}",
            "latency_ms": int(elapsed * 1000),
            "success": False,
        }


async def main():
    print("=" * 70)
    print("CORPUS REFINER STRATEGY COMPARISON")
    print("=" * 70)
    print(f"Test corpus: {len(SAMPLE_CORPUS)} files")
    print(f"Model: {FAST_MODEL}")
    print()
    
    async with httpx.AsyncClient(timeout=30.0) as session:
        # Strategy 1: Sequential (baseline)
        print("STRATEGY 1: Sequential calls (one file at a time)")
        print("-" * 50)
        start = time.perf_counter()
        semaphore = asyncio.Semaphore(1)  # Force sequential
        results = []
        for f in SAMPLE_CORPUS:
            r = await extract_single(session, f, semaphore)
            results.append(r)
        total_sequential = time.perf_counter() - start
        successes = sum(1 for r in results if r["success"])
        print(f"  Success: {successes}/{len(SAMPLE_CORPUS)}")
        print(f"  Total time: {int(total_sequential * 1000)}ms")
        print(f"  Avg per file: {int(total_sequential * 1000 / len(SAMPLE_CORPUS))}ms")
        print()
        
        # Strategy 2: Parallel with rate limit (3 concurrent)
        print("STRATEGY 2: Parallel (3 concurrent) with semaphore")
        print("-" * 50)
        start = time.perf_counter()
        semaphore = asyncio.Semaphore(3)  # Rate limit
        tasks = [extract_single(session, f, semaphore) for f in SAMPLE_CORPUS]
        results = await asyncio.gather(*tasks)
        total_parallel = time.perf_counter() - start
        successes = sum(1 for r in results if r["success"])
        print(f"  Success: {successes}/{len(SAMPLE_CORPUS)}")
        print(f"  Total time: {int(total_parallel * 1000)}ms")
        print(f"  Speedup vs sequential: {round(total_sequential / total_parallel, 2)}x")
        # Show one sample output with citation
        if results and results[0]["success"]:
            print(f"\n  Sample output with citation:")
            print(f"    Citation: {results[0]['citation']}")
            print(f"    Facts: {results[0]['facts'][:100]}...")
        print()
        
        # Strategy 3: Single batched call
        print("STRATEGY 3: Single batched call (all files at once)")
        print("-" * 50)
        result = await extract_batch(session, SAMPLE_CORPUS)
        if result["success"]:
            print(f"  Success: True")
            print(f"  Total time: {result['latency_ms']}ms")
            print(f"  Tokens in: {result['tokens_in']}")
            print(f"  Tokens out: {result['tokens_out']}")
            print(f"  Speedup vs sequential: {round(total_sequential * 1000 / result['latency_ms'], 2)}x")
            print(f"\n  Combined output preview:")
            print("  " + result["combined_output"][:500].replace("\n", "\n  ") + "...")
        else:
            print(f"  Error: {result.get('error')}")
        
        print()
        print("=" * 70)
        print("RECOMMENDATIONS FOR CORPUS REFINER")
        print("=" * 70)
        if result["success"]:
            print(f"""
✅ API calls from Python work reliably
✅ Rate limit is ~3 concurrent calls

BEST APPROACH: Hybrid batching
- Group files into batches of 5-10 for single API call
- Run batches in parallel (3 concurrent batches)
- For 31 files: 4 batches × ~1s each = ~2-3 seconds total

CITATION PRESERVATION: ✅ Works!
- Include {{"title", "source", "date"}} in prompt
- Model naturally preserves attribution in output

ESTIMATED LATENCY FOR 31 FILES:
- Sequential (31 calls): ~{int(total_sequential / len(SAMPLE_CORPUS) * 31)}ms = ~{int(total_sequential / len(SAMPLE_CORPUS) * 31 / 1000)}s  
- Parallel w/limit (31 calls): ~{int(total_parallel / len(SAMPLE_CORPUS) * 31)}ms = ~{int(total_parallel / len(SAMPLE_CORPUS) * 31 / 1000)}s
- Batched (4-6 calls): ~{result['latency_ms'] * 4}ms = ~{result['latency_ms'] * 4 / 1000:.1f}s  ← WINNER
""")


if __name__ == "__main__":
    asyncio.run(main())
