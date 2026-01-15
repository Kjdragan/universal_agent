#!/usr/bin/env python3
"""
Corpus Refiner v1.0

Distills a directory of research files into a consolidated, token-efficient
document while preserving citations and rich details.

Features:
- Batched API calls for efficiency (5-8 files per batch)
- Concurrent processing with rate limiting (max 3 concurrent)
- Universal extraction prompt that captures rich details (quotes, stats, themes)
- Citation preservation (title, source, date)
- Consolidated markdown output
"""

import argparse
import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import httpx
import yaml

# Configuration
BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
AUTH_TOKEN = os.getenv("ZAI_API_KEY", os.getenv("ANTHROPIC_AUTH_TOKEN"))
FAST_MODEL = os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "glm-4.7")

# Rate limit: max concurrent API calls
MAX_CONCURRENT = 3
# Files per batch (balance: too few = many calls, too many = long responses)
BATCH_SIZE = 5

# Detail level configuration
# Levels: "expanded" (default, rich detail), "accelerated" (fast, key facts only)
DETAIL_LEVEL = os.getenv("REFINER_DETAIL_LEVEL", "expanded")

# Prompts by detail level
EXTRACTION_PROMPTS = {
    "accelerated": """You are a research analyst extracting the ESSENCE of source documents.

Your task: Distill each article to its most valuable content for a research report.

## What to extract (in priority order):
1. **Specific facts with numbers** - statistics, dates, dollar amounts, percentages, counts
2. **Direct quotes** - named sources saying something notable (use quotation marks)
3. **Unique insights** - novel claims, contrarian views, expert opinions
4. **Concrete examples** - specific incidents, case studies, named entities
5. **Emerging themes** - patterns, trends, or tensions the article reveals

## What to AVOID:
- Generic statements anyone could write without reading the article
- Vague language ("many experts say", "significant impact")

## Output: 5-7 bullet points per article""",

    "expanded": """You are a research analyst performing COMPREHENSIVE extraction from source documents.

Your task: Capture ALL substantive content from each article for a detailed research report.
Do NOT over-compress - preserve important details and context.

## Extract EVERYTHING of value:

**PRIMARY (always include):**
- Statistics, numbers, percentages, dollar amounts, dates, timelines
- Direct quotes with speaker attribution
- Named individuals and their roles/titles
- Specific events, incidents, locations
- Key claims and their supporting evidence

**SECONDARY (include when present):**
- Background context that explains the situation
- Historical comparisons or precedents mentioned
- Cause-and-effect relationships
- Expert analysis or interpretation
- Reactions from different parties/stakeholders
- Implications or consequences discussed
- Interesting details that add color or depth

**TERTIARY (include if noteworthy):**
- Methodology notes (how figures were gathered)
- Caveats or limitations mentioned
- Contrarian or minority viewpoints
- Predictions or forecasts

## What to AVOID:
- Literal article structure ("The article discusses...")
- Subscription/paywall notices
- Navigation elements
- Generic truisms

## Output format for EACH article:
```
### [Article Title]
**Source:** [Source name], [Date]

[8-15 bullet points capturing the full substance of the article]
```

Preserve specificity - if the article says "5.2 million", write "5.2 million", not "millions".
"""
}

# Select prompt based on detail level
EXTRACTION_PROMPT = EXTRACTION_PROMPTS.get(DETAIL_LEVEL, EXTRACTION_PROMPTS["expanded"]) + "\n\nNow extract from these articles:\n\n"

# Max tokens by detail level
MAX_TOKENS_BY_LEVEL = {"accelerated": 2000, "expanded": 3500}


@dataclass
class ArticleMetadata:
    """Parsed article with metadata and content."""
    filepath: Path
    title: str
    source: str
    date: str
    word_count: int
    content: str

    def to_extraction_block(self) -> str:
        """Format for inclusion in extraction prompt."""
        # Truncate very long content to avoid overwhelming the model
        content = self.content
        if len(content) > 6000:
            content = content[:6000] + "\n[...truncated...]"
        
        return f"""---
**Article:** {self.title}
**Source:** {self.source}
**Date:** {self.date}

{content}
---
"""


def parse_article_file(filepath: Path) -> Optional[ArticleMetadata]:
    """Parse a markdown file with YAML frontmatter."""
    try:
        content = filepath.read_text(encoding="utf-8")
        
        # Extract YAML frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1])
                body = parts[2].strip()
                
                # Clean up body - remove navigation cruft
                # Remove lines that are just links or social sharing
                clean_lines = []
                for line in body.split("\n"):
                    # Skip share buttons, cookie notices, etc.
                    if any(skip in line.lower() for skip in [
                        "share on", "share at", "share in email", 
                        "cookie", "subscribe", "sign in",
                        "this is a subscriber-only", "paywall",
                        "](https:", "javascript:;"
                    ]):
                        continue
                    clean_lines.append(line)
                body = "\n".join(clean_lines)
                
                # Extract source domain from URL
                source_url = frontmatter.get("source", "")
                source_domain = re.search(r"https?://(?:www\.)?([^/]+)", source_url)
                source_name = source_domain.group(1) if source_domain else source_url
                
                return ArticleMetadata(
                    filepath=filepath,
                    title=frontmatter.get("title", filepath.stem),
                    source=source_name,
                    date=str(frontmatter.get("date", "unknown")),
                    word_count=frontmatter.get("word_count", 0),
                    content=body
                )
    except Exception as e:
        print(f"  Warning: Could not parse {filepath.name}: {e}")
    return None


async def extract_batch(
    session: httpx.AsyncClient, 
    articles: list[ArticleMetadata],
    semaphore: asyncio.Semaphore
) -> dict:
    """Extract key content from a batch of articles in a single API call."""
    async with semaphore:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": AUTH_TOKEN,
            "anthropic-version": "2023-06-01",
        }
        
        # Build combined prompt with all articles
        combined_articles = "\n\n".join(a.to_extraction_block() for a in articles)
        
        payload = {
            "model": FAST_MODEL,
            "max_tokens": MAX_TOKENS_BY_LEVEL.get(DETAIL_LEVEL, 2000),
            "messages": [
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT + combined_articles
                }
            ]
        }
        
        start = time.perf_counter()
        try:
            response = await session.post(
                f"{BASE_URL}/v1/messages",
                headers=headers,
                json=payload
            )
            elapsed = time.perf_counter() - start
            
            if response.status_code == 200:
                data = response.json()
                content = data.get("content", [{}])[0].get("text", "")
                return {
                    "success": True,
                    "batch_size": len(articles),
                    "output": content,
                    "latency_ms": int(elapsed * 1000),
                    "tokens_in": data.get("usage", {}).get("input_tokens", 0),
                    "tokens_out": data.get("usage", {}).get("output_tokens", 0),
                }
            elif response.status_code == 429:
                # Rate limited - wait and retry
                await asyncio.sleep(2)
                return await extract_batch(session, articles, semaphore)
            else:
                return {
                    "success": False,
                    "batch_size": len(articles),
                    "error": f"HTTP {response.status_code}: {response.text[:200]}",
                    "latency_ms": int(elapsed * 1000),
                }
        except Exception as e:
            return {
                "success": False,
                "batch_size": len(articles),
                "error": str(e),
                "latency_ms": 0,
            }


async def refine_corpus(
    corpus_dir: Path,
    output_file: Path,
    verbose: bool = True
) -> dict:
    """
    Process all markdown files in corpus_dir and produce a consolidated output.
    
    Returns metrics dict with token counts, latency, etc.
    """
    print(f"\n{'='*70}")
    print(f"CORPUS REFINER v1.0")
    print(f"{'='*70}")
    print(f"Input:  {corpus_dir}")
    print(f"Output: {output_file}")
    print(f"Model:  {FAST_MODEL}")
    print(f"Detail: {DETAIL_LEVEL} (max_tokens={MAX_TOKENS_BY_LEVEL.get(DETAIL_LEVEL, 2000)})")
    print()
    
    # 1. Parse all articles
    md_files = sorted(corpus_dir.glob("*.md"))
    print(f"Found {len(md_files)} markdown files")
    
    articles: list[ArticleMetadata] = []
    total_words = 0
    for f in md_files:
        article = parse_article_file(f)
        if article:
            articles.append(article)
            total_words += article.word_count
    
    print(f"Parsed {len(articles)} articles ({total_words:,} words total)")
    
    # 2. Create batches
    batches = []
    for i in range(0, len(articles), BATCH_SIZE):
        batches.append(articles[i:i + BATCH_SIZE])
    print(f"Created {len(batches)} batches (size={BATCH_SIZE})")
    
    # 3. Process batches concurrently with rate limiting
    print(f"\nProcessing with max {MAX_CONCURRENT} concurrent calls...")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    
    start_time = time.perf_counter()
    async with httpx.AsyncClient(timeout=60.0) as session:
        tasks = [extract_batch(session, batch, semaphore) for batch in batches]
        results = await asyncio.gather(*tasks)
    total_time = time.perf_counter() - start_time
    
    # 4. Aggregate results
    successful_outputs = []
    total_tokens_in = 0
    total_tokens_out = 0
    
    for i, result in enumerate(results):
        if result["success"]:
            successful_outputs.append(result["output"])
            total_tokens_in += result.get("tokens_in", 0)
            total_tokens_out += result.get("tokens_out", 0)
            if verbose:
                print(f"  Batch {i+1}: ✓ {result['batch_size']} articles in {result['latency_ms']}ms")
        else:
            if verbose:
                print(f"  Batch {i+1}: ✗ {result.get('error', 'Unknown error')}")
    
    # 5. Generate consolidated output
    header = f"""# Research Corpus Summary

**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S')}
**Source Directory:** {corpus_dir}
**Articles Processed:** {len(articles)}
**Original Word Count:** {total_words:,}

---

"""
    
    # Combine all batch outputs
    combined_content = "\n\n".join(successful_outputs)
    
    # Add sources list at the end
    sources_section = "\n\n---\n\n## Sources\n\n"
    for i, article in enumerate(articles, 1):
        sources_section += f"{i}. **{article.title}** — {article.source}, {article.date}\n"
    
    full_output = header + combined_content + sources_section
    
    # Write output
    output_file.write_text(full_output, encoding="utf-8")
    
    # 6. Calculate metrics
    output_words = len(full_output.split())
    compression_ratio = round(total_words / output_words, 1) if output_words else 0
    
    metrics = {
        "articles_processed": len(articles),
        "batches": len(batches),
        "successful_batches": len(successful_outputs),
        "original_words": total_words,
        "output_words": output_words,
        "compression_ratio": compression_ratio,
        "total_time_ms": int(total_time * 1000),
        "tokens_in": total_tokens_in,
        "tokens_out": total_tokens_out,
        "output_file": str(output_file),
    }
    
    print(f"\n{'='*70}")
    print(f"RESULTS")
    print(f"{'='*70}")
    print(f"Articles:    {metrics['articles_processed']}")
    print(f"Input:       {metrics['original_words']:,} words (~{metrics['original_words'] * 4 // 3:,} tokens)")
    print(f"Output:      {metrics['output_words']:,} words (~{metrics['output_words'] * 4 // 3:,} tokens)")
    print(f"Compression: {metrics['compression_ratio']}x")
    print(f"Time:        {metrics['total_time_ms'] / 1000:.1f}s")
    print(f"Output:      {output_file}")
    print(f"{'='*70}\n")
    
    return metrics


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Refine a research corpus into a token-efficient summary document.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default expanded mode (rich details, ~60s for 30 files)
  python corpus_refiner.py /path/to/corpus
  
  # Accelerated mode (key facts only, ~30s for 30 files)  
  python corpus_refiner.py /path/to/corpus --accelerated
  
  # Custom output path
  python corpus_refiner.py /path/to/corpus -o /path/to/output.md
"""
    )
    parser.add_argument(
        "corpus_dir",
        type=Path,
        help="Directory containing markdown files to refine"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=None,
        help="Output file path (default: {corpus_dir}/../refined_corpus.md)"
    )
    parser.add_argument(
        "--accelerated",
        action="store_true",
        help="Use accelerated mode (faster, less detail - ~30s vs ~60s for 30 files)"
    )
    parser.add_argument(
        "--show-sample",
        action="store_true",
        default=True,
        help="Show a sample of the output after completion"
    )
    return parser.parse_args()


async def main():
    """Run corpus refiner with CLI arguments."""
    global DETAIL_LEVEL, EXTRACTION_PROMPT
    
    args = parse_args()
    
    # Set detail level based on CLI flag
    if args.accelerated:
        DETAIL_LEVEL = "accelerated"
        EXTRACTION_PROMPT = EXTRACTION_PROMPTS["accelerated"] + "\n\nNow extract from these articles:\n\n"
    
    # Determine output path
    output_file = args.output or (args.corpus_dir.parent / "refined_corpus.md")
    
    # Run refiner
    metrics = await refine_corpus(args.corpus_dir, output_file)
    
    # Show sample if requested
    if args.show_sample:
        print("\n--- OUTPUT SAMPLE (first 2000 chars) ---\n")
        sample = output_file.read_text()[:2000]
        print(sample)
        print("\n[...continues...]\n")
    
    return metrics


# Convenience function for programmatic use by agents
async def refine_corpus_programmatic(
    corpus_dir: Path,
    output_file: Path = None,
    accelerated: bool = False
) -> dict:
    """
    Programmatic interface for agents to call the corpus refiner.
    
    Args:
        corpus_dir: Path to directory containing markdown files
        output_file: Optional output path (default: corpus_dir/../refined_corpus.md)
        accelerated: If True, use faster extraction with less detail
        
    Returns:
        Metrics dict with compression stats, latency, etc.
    """
    global DETAIL_LEVEL, EXTRACTION_PROMPT
    
    if accelerated:
        DETAIL_LEVEL = "accelerated"
        EXTRACTION_PROMPT = EXTRACTION_PROMPTS["accelerated"] + "\n\nNow extract from these articles:\n\n"
    else:
        DETAIL_LEVEL = "expanded"
        EXTRACTION_PROMPT = EXTRACTION_PROMPTS["expanded"] + "\n\nNow extract from these articles:\n\n"
    
    output_path = output_file or (corpus_dir.parent / "refined_corpus.md")
    return await refine_corpus(corpus_dir, output_path, verbose=True)


if __name__ == "__main__":
    asyncio.run(main())
