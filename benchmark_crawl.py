import asyncio
import json
import os
import sys

# Add src to path
sys.path.append(os.path.abspath("src"))


def get_urls():
    """Extract URLs from the provided JSON file."""
    json_path = "/home/kjdragan/lrepos/universal_agent/src/AGENT_RUN_WORKSPACES/session_20251223_082418/search_results/COMPOSIO_SEARCH_NEWS_0_082536.json"

    with open(json_path, "r") as f:
        data = json.load(f)

    urls = []

    # 1. Try Top-Level 'articles' list (Flattened Format)
    if "articles" in data:
        for item in data["articles"]:
            if item.get("url"):
                urls.append(item["url"])

    # 2. Try 'news_results' (Raw API Format)
    elif "news_results" in data.get("response", {}).get("data", {}):
        for item in data["response"]["data"]["news_results"]:
            if item.get("link"):
                urls.append(item["link"])

    # 3. Try 'organic_results'
    if len(urls) < 10 and "organic_results" in data.get("response", {}).get("data", {}):
        for item in data["response"]["data"]["organic_results"]:
            if item.get("link"):
                urls.append(item["link"])

    return urls[:10]  # Limit to 10 for the batch test


async def run_benchmark():
    urls = get_urls()
    print(f"🚀 Loaded {len(urls)} URLs for benchmarking:")
    for i, url in enumerate(urls, 1):
        print(f"  {i}. {url}")

    print("\nStarting Parallel Crawl...")

    # Import tool logic directly
    try:
        from mcp_server import crawl_parallel
    except ImportError:
        print("❌ Could not import crawl_parallel from src/mcp_server.py")
        return

    # Target specific session dir requested by user
    session_dir = "/home/kjdragan/lrepos/universal_agent/src/AGENT_RUN_WORKSPACES/session_20251223_082418"
    os.makedirs(session_dir, exist_ok=True)
    print(f"📂 Saving results to: {os.path.join(session_dir, 'search_results')}")

    import time

    t0 = time.time()

    result_json = await crawl_parallel(urls, session_dir)
    duration = time.time() - t0

    result = json.loads(result_json)

    print(f"\n📊 BENCHMARK RESULTS ({duration:.2f}s total)")
    print(f"✅ Successful: {result.get('successful')}")
    print(f"❌ Failed:     {result.get('failed')}")

    if result.get("failed") > 0:
        print("\nFailures:")
        for err in result.get("errors", []):
            print(f"  - {err['url']}: {err['error']}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
