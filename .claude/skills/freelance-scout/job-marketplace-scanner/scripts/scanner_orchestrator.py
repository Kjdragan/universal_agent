"""
Freelance Scout Orchestrator

Main entry point for running marketplace scans, scoring opportunities,
and generating intelligence digests. This script is what the freelance-scout
sub-agent invokes to execute a complete scan cycle.

Usage:
    # Full scan across all configured platforms
    python scanner_orchestrator.py --mode full

    # Quick scan (single platform, recent jobs only)  
    python scanner_orchestrator.py --mode quick --platform upwork

    # Generate digest from cached scan results
    python scanner_orchestrator.py --mode digest --input results/latest_scan.json

    # Health check all platform connections
    python scanner_orchestrator.py --mode health

Configuration:
    Environment variables for each platform (see platform_adapters.py)
    Or a config.json file in the working directory.

Output:
    - JSON scan results in results/ directory
    - Daily digest in results/ directory
    - Summary printed to stdout for the agent to parse
"""

import os
import sys
import json
import asyncio
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Add script directories to path
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "job-marketplace-scanner" / "scripts"))
sys.path.insert(0, str(SCRIPT_DIR.parent / "opportunity-analyzer" / "scripts"))

from models import Opportunity, ScanResult, Platform, DailyDigest
from platform_adapters import get_adapter, get_all_adapters
from scoring_engine import OpportunityScoringEngine, DigestGenerator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger("freelance_scout")

# Default search queries aligned with barbell strategy
DEFAULT_SEARCH_QUERIES = {
    # Quick wins (high volume, lower complexity)
    "quick_wins": [
        "data entry automation",
        "web scraping python",
        "spreadsheet automation",
        "research report",
        "data cleaning",
        "content writing blog",
        "technical documentation",
    ],
    # High value (premium, differentiating)
    "high_value": [
        "AI agent development",
        "LLM integration python",
        "machine learning pipeline",
        "data science consulting",
        "API automation workflow",
        "python automation consulting",
        "chatbot development langchain",
    ],
    # Growth opportunities (building toward)
    "growth": [
        "full stack python next.js",
        "cloud automation GCP AWS",
        "AI workflow automation",
        "multi-agent system",
    ],
}

RESULTS_DIR = Path("results")


async def run_full_scan(queries: dict = None, platforms: list = None) -> list[Opportunity]:
    """
    Execute a full scan across all platforms and query categories.
    
    Returns all discovered opportunities (unscored).
    """
    queries = queries or DEFAULT_SEARCH_QUERIES
    all_opportunities = []
    dedup_fingerprints = set()

    # Get adapters for requested platforms
    if platforms:
        adapters = {p: get_adapter(p) for p in platforms}
    else:
        adapters = get_all_adapters()

    logger.info(f"Scanning {len(adapters)} platform(s): {list(adapters.keys())}")

    for platform_name, adapter in adapters.items():
        logger.info(f"\n{'='*60}")
        logger.info(f"Scanning: {platform_name}")
        logger.info(f"{'='*60}")

        for category, query_list in queries.items():
            for query in query_list:
                logger.info(f"  [{category}] Searching: '{query}'")
                try:
                    result = await adapter.search(
                        query=query,
                        filters={"posted_within": "7d"},
                        page_size=20,
                    )

                    # Dedup across queries
                    new_count = 0
                    for opp in result.opportunities:
                        if opp.fingerprint not in dedup_fingerprints:
                            dedup_fingerprints.add(opp.fingerprint)
                            all_opportunities.append(opp)
                            new_count += 1

                    logger.info(
                        f"    Found {len(result.opportunities)} results, "
                        f"{new_count} new unique opportunities"
                    )

                    if result.errors:
                        for err in result.errors:
                            logger.warning(f"    Error: {err}")

                    if result.metadata.get('search_url'):
                        logger.info(f"    Search URL: {result.metadata['search_url']}")

                except Exception as e:
                    logger.error(f"    Scan failed: {e}")

    logger.info(f"\nTotal unique opportunities discovered: {len(all_opportunities)}")
    return all_opportunities


async def run_quick_scan(platform: str = "upwork", 
                          queries: list = None) -> list[Opportunity]:
    """Quick scan on a single platform with focused queries."""
    queries = queries or DEFAULT_SEARCH_QUERIES.get("quick_wins", [])[:3]
    adapter = get_adapter(platform)
    all_opps = []
    dedup = set()

    for query in queries:
        logger.info(f"Quick scan [{platform}]: '{query}'")
        try:
            result = await adapter.search(query=query, page_size=10)
            for opp in result.opportunities:
                if opp.fingerprint not in dedup:
                    dedup.add(opp.fingerprint)
                    all_opps.append(opp)
        except Exception as e:
            logger.error(f"Quick scan error: {e}")

    return all_opps


async def run_health_check():
    """Check connectivity and credentials for all platforms."""
    adapters = get_all_adapters()
    results = {}

    for name, adapter in adapters.items():
        logger.info(f"Checking {name}...")
        status = await adapter.health_check()
        results[name] = status
        
        # Pretty print status
        configured = status.get('api_configured', False)
        available = status.get('api_available') or status.get('graphql_available', False)
        errors = status.get('errors', [])
        
        icon = "✅" if available else ("⚠️" if configured else "❌")
        logger.info(f"  {icon} {name}: configured={configured}, available={available}")
        if errors:
            for err in errors:
                logger.info(f"     Error: {err}")

    return results


def score_and_digest(opportunities: list[Opportunity]) -> tuple[list[Opportunity], DailyDigest]:
    """Score all opportunities and generate a daily digest."""
    engine = OpportunityScoringEngine()
    scored = engine.score_batch(opportunities)

    generator = DigestGenerator()
    digest = generator.generate(scored)

    return scored, digest


def save_results(opportunities: list[Opportunity], digest: DailyDigest, 
                 output_dir: Path = None):
    """Save scan results and digest to JSON files."""
    output_dir = output_dir or RESULTS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    # Save full scan results
    scan_file = output_dir / f"scan_{timestamp}.json"
    scan_data = {
        'timestamp': datetime.utcnow().isoformat(),
        'total_opportunities': len(opportunities),
        'opportunities': [o.to_dict() for o in opportunities],
    }
    scan_file.write_text(json.dumps(scan_data, indent=2, default=str))
    logger.info(f"Scan results saved: {scan_file}")

    # Save digest
    digest_file = output_dir / f"digest_{timestamp}.json"
    digest_file.write_text(digest.to_json())
    logger.info(f"Digest saved: {digest_file}")

    # Save latest symlink/copy for easy access
    latest_scan = output_dir / "latest_scan.json"
    latest_scan.write_text(json.dumps(scan_data, indent=2, default=str))

    latest_digest = output_dir / "latest_digest.json"
    latest_digest.write_text(digest.to_json())

    return scan_file, digest_file


def print_digest_summary(digest: DailyDigest):
    """Print a human-readable digest summary to stdout."""
    print(f"\n{'='*70}")
    print(f"  FREELANCE SCOUT DAILY DIGEST — {digest.date}")
    print(f"{'='*70}")
    print(f"\n📊 OVERVIEW")
    print(f"  Total scanned: {digest.total_scanned}")
    print(f"  New today: {digest.new_opportunities}")
    print(f"  Shortlisted: {len(digest.shortlisted)}")

    if digest.platform_breakdown:
        print(f"\n📱 PLATFORM BREAKDOWN")
        for platform, count in digest.platform_breakdown.items():
            print(f"  • {platform}: {count} opportunities")

    if digest.shortlisted:
        print(f"\n⭐ TOP OPPORTUNITIES")
        for i, opp in enumerate(digest.shortlisted[:10], 1):
            score = opp.overall_score or 0
            value = opp.budget.estimated_value
            value_str = f"${value:,.0f}" if value else "N/A"
            comp = opp.proposals_count
            comp_str = f"{comp} bids" if comp is not None else "N/A"
            print(f"\n  {i}. [{score:.2f}] {opp.title}")
            print(f"     Platform: {opp.platform.value} | Value: {value_str} | Competition: {comp_str}")
            print(f"     Skills: {', '.join(opp.skills_required[:5])}")
            if opp.url:
                print(f"     URL: {opp.url}")
            if opp.analysis_notes:
                print(f"     Notes: {opp.analysis_notes}")

    if digest.top_skills_demanded:
        print(f"\n🔧 TOP SKILLS IN DEMAND")
        for skill, count in list(digest.top_skills_demanded.items())[:10]:
            print(f"  • {skill}: {count} mentions")

    if digest.capability_gaps:
        print(f"\n⚠️  CAPABILITY GAPS (skills to develop)")
        for gap in digest.capability_gaps[:5]:
            print(f"  • {gap}")

    if digest.trend_signals:
        print(f"\n📈 TREND SIGNALS")
        for signal in digest.trend_signals:
            print(f"  • {signal}")

    if digest.recommendations:
        print(f"\n💡 RECOMMENDATIONS")
        for rec in digest.recommendations:
            print(f"  • {rec}")

    print(f"\n{'='*70}\n")


async def main():
    parser = argparse.ArgumentParser(description="Freelance Scout - Marketplace Scanner")
    parser.add_argument('--mode', choices=['full', 'quick', 'digest', 'health'],
                        default='full', help='Scan mode')
    parser.add_argument('--platform', type=str, help='Platform for quick scan')
    parser.add_argument('--input', type=str, help='Input file for digest mode')
    parser.add_argument('--output', type=str, default='results',
                        help='Output directory')
    parser.add_argument('--queries', type=str, help='Custom queries JSON file')
    args = parser.parse_args()

    output_dir = Path(args.output)

    if args.mode == 'health':
        results = await run_health_check()
        print(json.dumps(results, indent=2))
        return

    if args.mode == 'digest':
        # Load existing scan results
        if not args.input:
            input_file = output_dir / "latest_scan.json"
        else:
            input_file = Path(args.input)
        
        if not input_file.exists():
            logger.error(f"Input file not found: {input_file}")
            return

        data = json.loads(input_file.read_text())
        opportunities = [Opportunity.from_dict(o) for o in data.get('opportunities', [])]
        scored, digest = score_and_digest(opportunities)
        print_digest_summary(digest)
        save_results(scored, digest, output_dir)
        return

    # Load custom queries if provided
    queries = None
    if args.queries:
        queries = json.loads(Path(args.queries).read_text())

    # Run scan
    if args.mode == 'quick':
        platform = args.platform or 'upwork'
        opportunities = await run_quick_scan(platform=platform)
    else:
        platforms = [args.platform] if args.platform else None
        opportunities = await run_full_scan(queries=queries, platforms=platforms)

    if not opportunities:
        logger.warning("No opportunities found. Check platform credentials and connectivity.")
        # Still generate empty digest for reporting
        scored, digest = score_and_digest([])
        print_digest_summary(digest)
        return

    # Score and generate digest
    scored, digest = score_and_digest(opportunities)
    print_digest_summary(digest)
    save_results(scored, digest, output_dir)

    logger.info("Scan complete.")


if __name__ == "__main__":
    asyncio.run(main())
