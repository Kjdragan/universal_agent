# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "speedtest-cli",
# ]
# ///
"""
Internet Speed Test Script
Uses the speedtest-cli library to measure download, upload, and ping.

Run with: uv run speedtest_runner.py
"""

import argparse
import json
import speedtest
from datetime import datetime


def bytes_to_megabits(bytes_per_second: float) -> float:
    """Convert bytes per second to megabits per second."""
    return bytes_per_second * 8 / 1_000_000


def run_speedtest(server_id: int | None = None, verbose: bool = True) -> dict:
    """
    Run a complete speed test.
    
    Args:
        server_id: Optional specific server ID to test against
        verbose: Whether to print progress messages
    
    Returns:
        Dictionary containing test results
    """
    st = speedtest.Speedtest()
    
    if verbose:
        print("=" * 50)
        print("        INTERNET SPEED TEST")
        print("=" * 50)
        print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-" * 50)
    
    # Get best server
    if verbose:
        print("Finding best server...")
    
    if server_id:
        # Use specific server
        servers = st.get_servers([server_id])
        st.get_best_server()
    else:
        st.get_best_server()
    
    server = st.results.server
    
    if verbose:
        print(f"Server:   {server['sponsor']} ({server['name']}, {server['country']})")
        print(f"Host:     {server['host']}")
        print(f"Distance: {server['d']:.2f} km")
        print("-" * 50)
    
    # Test ping (already done during server selection)
    ping = st.results.ping
    if verbose:
        print(f"Ping:     {ping:.2f} ms")
    
    # Test download
    if verbose:
        print("Testing download speed...", end=" ", flush=True)
    download_speed = st.download()
    download_mbps = bytes_to_megabits(download_speed)
    if verbose:
        print(f"{download_mbps:.2f} Mbit/s")
    
    # Test upload
    if verbose:
        print("Testing upload speed...", end=" ", flush=True)
    upload_speed = st.upload()
    upload_mbps = bytes_to_megabits(upload_speed)
    if verbose:
        print(f"{upload_mbps:.2f} Mbit/s")
    
    if verbose:
        print("-" * 50)
        print("RESULTS SUMMARY")
        print("-" * 50)
        print(f"  Ping:     {ping:.2f} ms")
        print(f"  Download: {download_mbps:.2f} Mbit/s")
        print(f"  Upload:   {upload_mbps:.2f} Mbit/s")
        print("=" * 50)
    
    # Compile results
    results = {
        "timestamp": datetime.now().isoformat(),
        "server": {
            "name": server["name"],
            "sponsor": server["sponsor"],
            "country": server["country"],
            "host": server["host"],
            "distance_km": round(server["d"], 2),
        },
        "ping_ms": round(ping, 2),
        "download_mbps": round(download_mbps, 2),
        "upload_mbps": round(upload_mbps, 2),
        "download_bytes_per_sec": download_speed,
        "upload_bytes_per_sec": upload_speed,
    }
    
    return results


def list_servers(limit: int = 10) -> None:
    """List nearby servers sorted by distance."""
    print("Finding nearby servers...")
    st = speedtest.Speedtest()
    st.get_servers()
    
    # Flatten server dict and sort by distance
    servers = []
    for server_list in st.servers.values():
        servers.extend(server_list)
    servers.sort(key=lambda x: x["d"])
    
    print(f"\nTop {limit} nearest servers:")
    print("-" * 70)
    print(f"{'ID':<8} {'Sponsor':<30} {'Location':<20} {'Distance':<10}")
    print("-" * 70)
    
    for server in servers[:limit]:
        sponsor = server["sponsor"][:28] + ".." if len(server["sponsor"]) > 30 else server["sponsor"]
        location = f"{server['name']}, {server['cc']}"
        location = location[:18] + ".." if len(location) > 20 else location
        print(f"{server['id']:<8} {sponsor:<30} {location:<20} {server['d']:.2f} km")


def main():
    parser = argparse.ArgumentParser(
        description="Test your internet connection speed.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run speedtest_runner.py              # Run standard test
  uv run speedtest_runner.py --json       # Output as JSON
  uv run speedtest_runner.py --list       # List nearby servers
  uv run speedtest_runner.py --server 123 # Test against specific server
        """
    )
    
    parser.add_argument(
        "--json", 
        action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--list", 
        action="store_true",
        help="List nearby servers and exit"
    )
    parser.add_argument(
        "--server", 
        type=int,
        help="Specify server ID to test against"
    )
    parser.add_argument(
        "--output", 
        type=str,
        help="Save results to a JSON file"
    )
    
    args = parser.parse_args()
    
    if args.list:
        list_servers()
        return
    
    # Run the test
    verbose = not args.json
    results = run_speedtest(server_id=args.server, verbose=verbose)
    
    # Handle output
    if args.json:
        print(json.dumps(results, indent=2))
    
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        if verbose:
            print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    main()
