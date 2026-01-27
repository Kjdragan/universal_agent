#!/usr/bin/env python3
"""
Baseline Comparison Script

Runs the same prompt through CLI and Gateway paths to establish behavioral baseline.
Records: tool call count, output paths, completion status, duration.

Usage:
    python scripts/baseline_comparison.py [--prompt "custom prompt"]
    
Results saved to: scripts/baseline_comparison_results.json
"""

import asyncio
import json
import os
import sys
import time
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

BASELINE_PROMPT = "Write a brief summary of your capabilities to work_products/summary.md"


async def run_cli_path(prompt: str, workspace_dir: Path) -> dict[str, Any]:
    """Run prompt through the CLI process_turn path."""
    from universal_agent.main import process_turn, setup_session
    from claude_agent_sdk.client import ClaudeSDKClient
    
    result_data = {
        "path": "cli_process_turn",
        "workspace": str(workspace_dir),
        "prompt": prompt,
        "start_time": datetime.now().isoformat(),
        "tool_calls": 0,
        "output_files": [],
        "status": "unknown",
        "error": None,
        "duration_seconds": 0,
    }
    
    start = time.time()
    
    try:
        # Setup session
        options, session, user_id, ws_dir, trace = await setup_session(
            workspace_dir_override=str(workspace_dir),
        )
        
        # Create client and run
        async with ClaudeSDKClient(options) as client:
            execution_result = await process_turn(
                client=client,
                user_input=prompt,
                workspace_dir=str(workspace_dir),
                force_complex=False,
                max_iterations=10,
            )
            
            result_data["tool_calls"] = execution_result.tool_calls if hasattr(execution_result, 'tool_calls') else 0
            result_data["status"] = "success" if execution_result.success else "failed"
            result_data["response_preview"] = (execution_result.response_text or "")[:500]
            
    except Exception as e:
        result_data["status"] = "error"
        result_data["error"] = str(e)
    
    result_data["duration_seconds"] = round(time.time() - start, 2)
    result_data["end_time"] = datetime.now().isoformat()
    
    # Scan for output files
    work_products = workspace_dir / "work_products"
    if work_products.exists():
        result_data["output_files"] = [
            str(f.relative_to(workspace_dir)) for f in work_products.rglob("*") if f.is_file()
        ]
    
    return result_data


async def run_gateway_path(prompt: str, workspace_dir: Path) -> dict[str, Any]:
    """Run prompt through the Gateway path (current implementation)."""
    from universal_agent.gateway import InProcessGateway, GatewayRequest
    from universal_agent.agent_core import EventType
    
    result_data = {
        "path": "gateway_inprocess",
        "workspace": str(workspace_dir),
        "prompt": prompt,
        "start_time": datetime.now().isoformat(),
        "tool_calls": 0,
        "output_files": [],
        "status": "unknown",
        "error": None,
        "duration_seconds": 0,
    }
    
    start = time.time()
    
    try:
        gateway = InProcessGateway()
        session = await gateway.create_session(
            user_id="baseline_test",
            workspace_dir=str(workspace_dir),
        )
        
        request = GatewayRequest(user_input=prompt)
        response_text = ""
        tool_calls = 0
        
        async for event in gateway.execute(session, request):
            if event.type == EventType.TOOL_CALL:
                tool_calls += 1
            elif event.type == EventType.TEXT:
                response_text += event.data.get("text", "")
        
        result_data["tool_calls"] = tool_calls
        result_data["status"] = "success"
        result_data["response_preview"] = response_text[:500]
        
    except Exception as e:
        result_data["status"] = "error"
        result_data["error"] = str(e)
    
    result_data["duration_seconds"] = round(time.time() - start, 2)
    result_data["end_time"] = datetime.now().isoformat()
    
    # Scan for output files
    work_products = workspace_dir / "work_products"
    if work_products.exists():
        result_data["output_files"] = [
            str(f.relative_to(workspace_dir)) for f in work_products.rglob("*") if f.is_file()
        ]
    
    return result_data


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Baseline comparison between CLI and Gateway paths")
    parser.add_argument("--prompt", default=BASELINE_PROMPT, help="Prompt to test")
    parser.add_argument("--skip-cli", action="store_true", help="Skip CLI path test")
    parser.add_argument("--skip-gateway", action="store_true", help="Skip Gateway path test")
    parser.add_argument("--cleanup", action="store_true", help="Clean up test workspaces after")
    args = parser.parse_args()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = Path(__file__).parent.parent / "AGENT_RUN_WORKSPACES" / f"baseline_test_{timestamp}"
    base_dir.mkdir(parents=True, exist_ok=True)
    
    results = {
        "test_timestamp": timestamp,
        "prompt": args.prompt,
        "paths_tested": [],
        "comparison": {},
    }
    
    print(f"\n{'='*60}")
    print("BASELINE COMPARISON TEST")
    print(f"{'='*60}")
    print(f"Prompt: {args.prompt[:80]}...")
    print(f"Base directory: {base_dir}")
    print()
    
    # Run CLI path
    if not args.skip_cli:
        print("📍 Testing CLI path (process_turn)...")
        cli_workspace = base_dir / "cli_test"
        cli_workspace.mkdir(parents=True, exist_ok=True)
        (cli_workspace / "work_products").mkdir(exist_ok=True)
        
        cli_result = await run_cli_path(args.prompt, cli_workspace)
        results["cli_path"] = cli_result
        results["paths_tested"].append("cli")
        
        print(f"   Status: {cli_result['status']}")
        print(f"   Tool calls: {cli_result['tool_calls']}")
        print(f"   Duration: {cli_result['duration_seconds']}s")
        print(f"   Output files: {cli_result['output_files']}")
        if cli_result['error']:
            print(f"   Error: {cli_result['error']}")
        print()
    
    # Run Gateway path
    if not args.skip_gateway:
        print("📍 Testing Gateway path (InProcessGateway)...")
        gateway_workspace = base_dir / "gateway_test"
        gateway_workspace.mkdir(parents=True, exist_ok=True)
        (gateway_workspace / "work_products").mkdir(exist_ok=True)
        
        gateway_result = await run_gateway_path(args.prompt, gateway_workspace)
        results["gateway_path"] = gateway_result
        results["paths_tested"].append("gateway")
        
        print(f"   Status: {gateway_result['status']}")
        print(f"   Tool calls: {gateway_result['tool_calls']}")
        print(f"   Duration: {gateway_result['duration_seconds']}s")
        print(f"   Output files: {gateway_result['output_files']}")
        if gateway_result['error']:
            print(f"   Error: {gateway_result['error']}")
        print()
    
    # Compare results
    if "cli_path" in results and "gateway_path" in results:
        cli = results["cli_path"]
        gw = results["gateway_path"]
        
        comparison = {
            "tool_calls_match": cli["tool_calls"] == gw["tool_calls"],
            "status_match": cli["status"] == gw["status"],
            "output_files_match": set(cli["output_files"]) == set(gw["output_files"]),
            "divergences": [],
        }
        
        if not comparison["tool_calls_match"]:
            comparison["divergences"].append(
                f"Tool calls differ: CLI={cli['tool_calls']}, Gateway={gw['tool_calls']}"
            )
        
        if not comparison["status_match"]:
            comparison["divergences"].append(
                f"Status differs: CLI={cli['status']}, Gateway={gw['status']}"
            )
        
        if not comparison["output_files_match"]:
            cli_files = set(cli["output_files"])
            gw_files = set(gw["output_files"])
            comparison["divergences"].append(
                f"Output files differ: CLI={cli_files}, Gateway={gw_files}"
            )
        
        results["comparison"] = comparison
        
        print(f"{'='*60}")
        print("COMPARISON RESULTS")
        print(f"{'='*60}")
        
        if comparison["divergences"]:
            print("❌ DIVERGENCES DETECTED:")
            for d in comparison["divergences"]:
                print(f"   - {d}")
        else:
            print("✅ NO DIVERGENCES - Paths behave identically")
        print()
    
    # Save results
    results_path = Path(__file__).parent / "baseline_comparison_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"📊 Results saved to: {results_path}")
    
    # Cleanup
    if args.cleanup:
        print(f"🧹 Cleaning up {base_dir}...")
        shutil.rmtree(base_dir, ignore_errors=True)
    
    return results


if __name__ == "__main__":
    asyncio.run(main())
