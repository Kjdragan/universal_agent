#!/usr/bin/env python
"""
Execute comprehensive web searches for Llama-3 70B fine-tuning research.
Uses MCP COMPOSIO_SEARCH_WEB tool via direct imports.
"""

import json
import asyncio
import sys
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from composio import ComposioToolSet, Action
    print("Successfully imported Composio tools")
except ImportError as e:
    print(f"Failed to import Composio: {e}")
    print("This script requires Composio to be installed")
    sys.exit(1)

# Search queries focused on Llama-3 70B fine-tuning for coding
SEARCH_QUERIES = [
    # Core hyperparameters
    "Llama-3 70B fine-tuning coding hyperparameters 2024",
    "Llama-3 learning rate batch size optimizer AdamW",

    # LoRA/PEFT specific
    "LoRA parameters code generation LLM optimal rank alpha",
    "PEFT LoRA coding tasks best practices 2024 2025",
    "QLoRA 70B model fine-tuning memory efficiency",

    # Tools and frameworks
    "LLaMA-Factory code generation configuration guide",
    "Axolotl Llama-3 70B fine-tuning YAML config",
    "Hugging Face TRL Llama-3 fine-tuning code",

    # Datasets and evaluation
    "code instruction dataset preparation quality filtering",
    "HumanEval MBPP fine-tuning benchmark code models",
    "code LLM instruction tuning dataset size balance",

    # GPU and resources
    "70B model fine-tuning GPU memory requirements cost",
    "Llama-3 70B fine-tuning A100 H100 training time",

    # Meta official sources
    "Meta AI Llama-3 fine-tuning technical blog",
    "Llama-3 paper code generation capabilities fine-tuning"
]

async def execute_web_search(toolset, query: str) -> dict:
    """Execute a single web search query."""
    print(f"\n🔍 Searching: {query}")

    try:
        result = await toolset.execute_action(
            action=Action.COMPOSIO_SEARCH_WEB,
            params={"query": query}
        )

        print(f"  ✓ Found results")

        return {
            "query": query,
            "timestamp": datetime.now().isoformat(),
            "status": "success",
            "results": result
        }

    except Exception as e:
        print(f"  ✗ Error: {e}")
        return {
            "query": query,
            "timestamp": datetime.now().isoformat(),
            "status": "error",
            "error": str(e)
        }

async def main():
    """Execute all searches and save results."""
    print("=" * 70)
    print("LLAMA-3 70B FINE-TUNING RESEARCH - WEB SEARCH")
    print("=" * 70)
    print(f"Total queries: {len(SEARCH_QUERIES)}")

    # Initialize toolset
    toolset = ComposioToolSet()

    # Execute searches (with rate limiting consideration)
    results = []
    for i, query in enumerate(SEARCH_QUERIES, 1):
        print(f"\n[{i}/{len(SEARCH_QUERIES)}]", end="")

        result = await execute_web_search(toolset, query)
        results.append(result)

        # Small delay to be respectful
        if i < len(SEARCH_QUERIES):
            await asyncio.sleep(0.5)

    # Save results
    output_dir = Path("/home/kjdragan/lrepos/universal_agent/search_results")
    output_dir.mkdir(exist_ok=True, parents=True)

    output_file = output_dir / "llama3_coding_finetuning_searches.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 70)
    print(f"✓ Saved {len(results)} search results to:")
    print(f"  {output_file}")
    print("=" * 70)

    # Summary
    success_count = sum(1 for r in results if r.get("status") == "success")
    error_count = sum(1 for r in results if r.get("status") == "error")

    print(f"\nSummary:")
    print(f"  Successful: {success_count}")
    print(f"  Failed:     {error_count}")

if __name__ == "__main__":
    asyncio.run(main())
