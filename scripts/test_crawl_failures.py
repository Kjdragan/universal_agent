#!/usr/bin/env python3
"""
Test script to re-run failed URLs against Crawl4AI Cloud API.
Extracts failed URLs from the session's finalize_research output and re-tests them.
"""

import asyncio
import aiohttp
import json
import os
import sys
from collections import Counter
from dotenv import load_dotenv

# Load from .env
load_dotenv("/home/kjdragan/lrepos/universal_agent/.env")

# Load API key
CRAWL4AI_API_KEY = os.environ.get("CRAWL4AI_API_KEY")
if not CRAWL4AI_API_KEY:
    print("ERROR: CRAWL4AI_API_KEY not set in environment")
    sys.exit(1)


CLOUD_ENDPOINT = "https://www.crawl4ai-cloud.com/query"

# Session directory with failed URLs
SESSION_DIR = "/home/kjdragan/lrepos/universal_agent/AGENT_RUN_WORKSPACES/session_20260106_154209"


def extract_failed_urls_from_search_results():
    """
    Extract URLs that didn't result in a crawl file from the research_overview.md
    by comparing search result URLs vs filtered corpus URLs.
    """
    search_results_dir = os.path.join(SESSION_DIR, "search_results")
    task_dir = os.path.join(SESSION_DIR, "tasks", "global_ai_regulation_2025")
    filtered_dir = os.path.join(task_dir, "filtered_corpus")
    processed_dir = os.path.join(search_results_dir, "processed_json")
    
    # Get all URLs that were crawled successfully (have a file in filtered_corpus OR search_results)
    successful_urls = set()
    
    # Check raw crawl files
    for f in os.listdir(search_results_dir):
        if f.startswith("crawl_") and f.endswith(".md"):
            path = os.path.join(search_results_dir, f)
            with open(path, "r") as file:
                content = file.read()
                # Extract URL from frontmatter
                if "source: " in content:
                    for line in content.split("\n"):
                        if line.startswith("source: "):
                            url = line.replace("source: ", "").strip()
                            successful_urls.add(url)
                            break
    
    # Get all URLs from search results
    all_search_urls = set()
    for f in os.listdir(processed_dir):
        if f.endswith(".json"):
            path = os.path.join(processed_dir, f)
            with open(path, "r") as file:
                try:
                    data = json.load(file)
                    # Extract URLs from various result formats
                    for key in ["results", "organic", "articles"]:
                        items = data.get(key, [])
                        if isinstance(items, list):
                            for item in items:
                                if isinstance(item, dict):
                                    for url_key in ["url", "link"]:
                                        url = item.get(url_key)
                                        if url and url.startswith("http"):
                                            all_search_urls.add(url)
                except Exception as e:
                    print(f"Error reading {f}: {e}")
    
    # Failed = URLs in search that don't have a crawl file
    failed_urls = all_search_urls - successful_urls
    
    # Filter out blacklisted 
    blacklist = ["wikipedia.org", "youtube.com", "twitter.com", "x.com", "facebook.com"]
    failed_urls = [u for u in failed_urls if not any(b in u for b in blacklist)]
    
    return list(failed_urls), len(successful_urls), len(all_search_urls)


async def test_single_url(session, url, idx, total):
    """Test a single URL against Crawl4AI API"""
    payload = {
        "url": url,
        "apikey": CRAWL4AI_API_KEY,
        "excluded_tags": ["nav", "footer", "header", "aside", "script", "style", "form"],
        "remove_overlay_elements": True,
        "word_count_threshold": 10,
        "cache_mode": "bypass",
        "magic": True,  # Anti-bot bypass
    }
    
    try:
        async with session.post(CLOUD_ENDPOINT, json=payload, timeout=60) as resp:
            if resp.status != 200:
                return {"url": url, "success": False, "error": f"HTTP {resp.status}", "content_len": 0}
            
            data = await resp.json()
            
            if data.get("success") == False:
                return {"url": url, "success": False, "error": data.get("error", "Unknown"), "content_len": 0}
            
            # Get content
            payload_data = data.get("data") if isinstance(data.get("data"), dict) else data
            content = (
                payload_data.get("content") or 
                payload_data.get("markdown") or 
                payload_data.get("fit_markdown") or 
                ""
            )
            
            # Check for Cloudflare block
            if len(content) < 2000 and ("cloudflare" in content.lower() or "verifying you are human" in content.lower()):
                return {"url": url, "success": False, "error": "Cloudflare blocked", "content_len": len(content)}
            
            if content and len(content) > 100:
                print(f"  [{idx}/{total}] ✅ {url[:60]}... ({len(content)} chars)")
                return {"url": url, "success": True, "error": None, "content_len": len(content)}
            else:
                return {"url": url, "success": False, "error": "Empty/short content", "content_len": len(content)}
                
    except asyncio.TimeoutError:
        return {"url": url, "success": False, "error": "Timeout (60s)", "content_len": 0}
    except Exception as e:
        return {"url": url, "success": False, "error": str(e), "content_len": 0}


async def main():
    print("=" * 70)
    print("CRAWL4AI FAILURE INVESTIGATION")
    print("=" * 70)
    
    # Extract failed URLs
    print("\n📊 Extracting URLs from session...")
    failed_urls, successful_count, total_count = extract_failed_urls_from_search_results()
    
    print(f"   Total search URLs: {total_count}")
    print(f"   Successfully crawled: {successful_count}")
    print(f"   Failed (to re-test): {len(failed_urls)}")
    
    if not failed_urls:
        print("\n✅ No failed URLs to re-test!")
        return
    
    # Sample if too many
    test_urls = failed_urls[:30] if len(failed_urls) > 30 else failed_urls
    print(f"\n🔄 Re-testing {len(test_urls)} URLs against Crawl4AI Cloud API...")
    
    async with aiohttp.ClientSession() as session:
        tasks = [test_single_url(session, url, i+1, len(test_urls)) for i, url in enumerate(test_urls)]
        results = await asyncio.gather(*tasks)
    
    # Analyze results
    error_counts = Counter()
    successes = 0
    failures = []
    
    for r in results:
        if r["success"]:
            successes += 1
        else:
            error_counts[r["error"]] += 1
            failures.append(r)
    
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    print(f"\n✅ Successful on retry: {successes}/{len(test_urls)}")
    print(f"❌ Still failing: {len(failures)}/{len(test_urls)}")
    
    print("\n📊 Error breakdown:")
    for error, count in error_counts.most_common():
        print(f"   - {error}: {count}")
    
    if failures:
        print("\n📋 Sample failures (first 10):")
        for f in failures[:10]:
            print(f"   {f['error']}: {f['url'][:70]}...")
    
    # Write full results to file
    output_path = os.path.join(SESSION_DIR, "crawl_failure_analysis.json")
    with open(output_path, "w") as f:
        json.dump({
            "test_time": "2026-01-06T15:58:00",
            "total_failed_urls": len(failed_urls),
            "tested_urls": len(test_urls),
            "successes_on_retry": successes,
            "still_failing": len(failures),
            "error_breakdown": dict(error_counts),
            "failures": failures,
        }, f, indent=2)
    print(f"\n💾 Full results saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
