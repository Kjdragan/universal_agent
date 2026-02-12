#!/usr/bin/env python3
"""
Test script to verify ZAI API connectivity for corpus refiner feasibility.
Tests: 1) Single API call, 2) Parallel API calls, 3) Fast model availability
"""

import asyncio
import os
import time
from pathlib import Path

# Load env vars from .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import httpx

# Configuration from environment
BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
AUTH_TOKEN = os.getenv("ZAI_API_KEY", os.getenv("ANTHROPIC_AUTH_TOKEN"))
MODEL = (
    os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL")
    or os.getenv("MODEL_NAME")
    or "glm-5"
)
HAIKU_MODEL = os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "glm-5")

print(f"=== ZAI API Feasibility Test ===")
print(f"Base URL: {BASE_URL}")
print(f"Auth Token: {AUTH_TOKEN[:20]}..." if AUTH_TOKEN else "Auth Token: NOT SET")
print(f"Sonnet Model: {MODEL}")
print(f"Haiku Model: {HAIKU_MODEL}")
print()


async def test_single_call(model: str, label: str) -> dict:
    """Test a single API call and measure latency."""
    headers = {
        "Content-Type": "application/json",
        "x-api-key": AUTH_TOKEN,
        "anthropic-version": "2023-06-01",
    }
    
    payload = {
        "model": model,
        "max_tokens": 200,
        "messages": [
            {
                "role": "user",
                "content": "Extract the key fact from this sentence and respond with ONLY that fact in under 20 words: 'The Oreshnik missile has a range of 5,500 km and travels at 12,000 km/h.'"
            }
        ]
    }
    
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{BASE_URL}/v1/messages",
                headers=headers,
                json=payload
            )
            elapsed = time.perf_counter() - start
            
            if response.status_code == 200:
                data = response.json()
                content = data.get("content", [{}])[0].get("text", "No content")
                return {
                    "success": True,
                    "label": label,
                    "model": model,
                    "latency_ms": int(elapsed * 1000),
                    "response": content[:100],
                    "tokens_in": data.get("usage", {}).get("input_tokens", 0),
                    "tokens_out": data.get("usage", {}).get("output_tokens", 0),
                }
            else:
                return {
                    "success": False,
                    "label": label,
                    "model": model,
                    "latency_ms": int(elapsed * 1000),
                    "error": f"HTTP {response.status_code}: {response.text[:200]}",
                }
    except Exception as e:
        elapsed = time.perf_counter() - start
        return {
            "success": False,
            "label": label,
            "model": model,
            "latency_ms": int(elapsed * 1000),
            "error": str(e),
        }


async def test_parallel_calls(model: str, count: int) -> dict:
    """Test multiple parallel API calls."""
    start = time.perf_counter()
    tasks = [test_single_call(model, f"parallel-{i}") for i in range(count)]
    results = await asyncio.gather(*tasks)
    total_elapsed = time.perf_counter() - start
    
    successes = sum(1 for r in results if r["success"])
    individual_latencies = [r["latency_ms"] for r in results if r["success"]]
    
    return {
        "total_calls": count,
        "successes": successes,
        "failures": count - successes,
        "total_time_ms": int(total_elapsed * 1000),
        "avg_individual_latency_ms": int(sum(individual_latencies) / len(individual_latencies)) if individual_latencies else 0,
        "speedup_vs_sequential": round(sum(individual_latencies) / (total_elapsed * 1000), 2) if individual_latencies else 0,
        "errors": [r.get("error") for r in results if not r["success"]],
    }


async def main():
    print("=" * 60)
    print("TEST 1: Single API Call (Sonnet)")
    print("=" * 60)
    result = await test_single_call(MODEL, "single-sonnet")
    for k, v in result.items():
        print(f"  {k}: {v}")
    
    print()
    print("=" * 60)
    print("TEST 2: Single API Call (Haiku / Fast Model)")
    print("=" * 60)
    result = await test_single_call(HAIKU_MODEL, "single-haiku")
    for k, v in result.items():
        print(f"  {k}: {v}")
    
    print()
    print("=" * 60)
    print("TEST 3: Parallel API Calls (5 concurrent)")
    print("=" * 60)
    result = await test_parallel_calls(MODEL, 5)
    for k, v in result.items():
        print(f"  {k}: {v}")
    
    print()
    print("=" * 60)
    print("CONCLUSIONS")
    print("=" * 60)
    if result["successes"] == result["total_calls"]:
        print("✅ API calls work from Python code")
        print(f"✅ Parallel calls work (speedup: {result['speedup_vs_sequential']}x)")
        print(f"📊 Typical latency: {result['avg_individual_latency_ms']}ms per call")
        if result["speedup_vs_sequential"] > 2:
            print("✅ Good parallelization - corpus refiner is feasible!")
        else:
            print("⚠️  Limited parallelization - may need rate limit workarounds")
    else:
        print("❌ Some API calls failed - check errors above")


if __name__ == "__main__":
    asyncio.run(main())
