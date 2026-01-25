#!/usr/bin/env python3
"""
Fetch and analyze all Letta agent memories.

This script retrieves memory blocks from all Universal Agent Letta agents
and produces a report on what's been learned/stored over time.

Usage:
    python letta/scripts/fetch_letta_memories.py
    python letta/scripts/fetch_letta_memories.py --output letta/reports/letta_memory_report.md
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Load .env file
try:
    from dotenv import load_dotenv
    # Look for .env in project root
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

try:
    from agentic_learning import AgenticLearning
except ImportError:
    print("❌ agentic_learning SDK not installed. Install with: pip install agentic-learning")
    sys.exit(1)


def fetch_all_agents(client: AgenticLearning) -> list[dict]:
    """Fetch all agents from Letta that match our naming pattern."""
    agents = []
    try:
        # List all agents
        response = client.agents.list()
        for agent in response:
            agent_data = {
                "id": getattr(agent, "id", None),
                "name": getattr(agent, "name", None),
                "created_at": str(getattr(agent, "created_at", "")),
                "model": getattr(agent, "model", None),
            }
            # Filter for universal_agent related agents
            name = agent_data.get("name", "") or ""
            if "universal_agent" in name.lower() or name.startswith("universal"):
                agents.append(agent_data)
    except Exception as e:
        print(f"⚠️ Error listing agents: {e}")
    return agents


def fetch_agent_memory(client: AgenticLearning, agent_name: str) -> dict:
    """Fetch all memory blocks for a specific agent."""
    memory_data = {
        "agent_name": agent_name,
        "blocks": [],
        "context": None,
        "error": None,
    }
    
    try:
        # Get memory blocks
        blocks = client.memory.list(agent=agent_name)
        for block in blocks:
            block_data = {
                "label": getattr(block, "label", None),
                "value": getattr(block, "value", None),
                "description": getattr(block, "description", None),
                "limit": getattr(block, "limit", None),
            }
            # Calculate usage
            value = block_data.get("value") or ""
            limit = block_data.get("limit") or 0
            block_data["char_count"] = len(value)
            block_data["usage_pct"] = round(len(value) / limit * 100, 1) if limit > 0 else 0
            memory_data["blocks"].append(block_data)
    except Exception as e:
        memory_data["error"] = f"Memory list error: {e}"
    
    try:
        # Get context (compiled memory)
        context = client.memory.context.retrieve(agent=agent_name)
        memory_data["context"] = context
        memory_data["context_length"] = len(context) if context else 0
    except Exception as e:
        if "error" not in memory_data or not memory_data["error"]:
            memory_data["error"] = f"Context error: {e}"
    
    return memory_data


def analyze_memory_usefulness(memory_data: dict) -> dict:
    """Analyze how useful the stored memory appears to be."""
    analysis = {
        "total_blocks": len(memory_data.get("blocks", [])),
        "non_empty_blocks": 0,
        "total_chars": 0,
        "useful_signals": [],
        "concerns": [],
        "recommendation": "",
    }
    
    for block in memory_data.get("blocks", []):
        value = block.get("value") or ""
        label = block.get("label") or ""
        char_count = len(value)
        
        if char_count > 0:
            analysis["non_empty_blocks"] += 1
            analysis["total_chars"] += char_count
            
            # Check for useful patterns
            if "recent_queries" in label and char_count > 50:
                analysis["useful_signals"].append(f"📋 {label}: Has query history ({char_count} chars)")
            elif "recent_reports" in label and char_count > 50:
                analysis["useful_signals"].append(f"📄 {label}: Has report history ({char_count} chars)")
            elif "project_context" in label and char_count > 50:
                analysis["useful_signals"].append(f"🏗️ {label}: Has project context ({char_count} chars)")
            elif "system_rules" in label and char_count > 50:
                analysis["useful_signals"].append(f"⚙️ {label}: Has system rules ({char_count} chars)")
            elif char_count > 100:
                analysis["useful_signals"].append(f"✅ {label}: Contains data ({char_count} chars)")
        
        # Check for concerns
        usage = block.get("usage_pct", 0)
        if usage > 80:
            analysis["concerns"].append(f"⚠️ {label}: Near capacity ({usage}% full)")
    
    # Generate recommendation
    if analysis["non_empty_blocks"] == 0:
        analysis["recommendation"] = "🔴 No memory accumulated - agent may not be learning"
    elif analysis["total_chars"] < 500:
        analysis["recommendation"] = "🟡 Minimal memory - early stage or not capturing much"
    elif len(analysis["useful_signals"]) >= 2:
        analysis["recommendation"] = "🟢 Good memory accumulation - appears useful"
    else:
        analysis["recommendation"] = "🟡 Some memory present - review content for relevance"
    
    return analysis


def generate_report(agents: list[dict], memories: list[dict]) -> str:
    """Generate a markdown report of all Letta memories."""
    lines = [
        "# Letta Memory Analysis Report",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Agents Found:** {len(agents)}",
        "",
        "---",
        "",
    ]
    
    # Summary table
    lines.extend([
        "## Summary",
        "",
        "| Agent | Blocks | Non-Empty | Total Chars | Status |",
        "|-------|--------|-----------|-------------|--------|",
    ])
    
    for mem in memories:
        analysis = analyze_memory_usefulness(mem)
        agent_name = mem.get("agent_name", "unknown")
        # Truncate long names
        display_name = agent_name[:40] + "..." if len(agent_name) > 40 else agent_name
        lines.append(
            f"| `{display_name}` | {analysis['total_blocks']} | "
            f"{analysis['non_empty_blocks']} | {analysis['total_chars']:,} | "
            f"{analysis['recommendation'].split(' ')[0]} |"
        )
    
    lines.extend(["", "---", ""])
    
    # Per-agent details
    for mem in memories:
        agent_name = mem.get("agent_name", "unknown")
        analysis = analyze_memory_usefulness(mem)
        
        lines.extend([
            f"## Agent: `{agent_name}`",
            "",
        ])
        
        if mem.get("error"):
            lines.append(f"**Error:** {mem['error']}")
            lines.append("")
            continue
        
        # Analysis summary
        lines.extend([
            "### Analysis",
            "",
            f"- **Recommendation:** {analysis['recommendation']}",
            f"- **Total Blocks:** {analysis['total_blocks']}",
            f"- **Non-Empty Blocks:** {analysis['non_empty_blocks']}",
            f"- **Total Characters:** {analysis['total_chars']:,}",
            "",
        ])
        
        if analysis["useful_signals"]:
            lines.append("**Useful Signals:**")
            for signal in analysis["useful_signals"]:
                lines.append(f"- {signal}")
            lines.append("")
        
        if analysis["concerns"]:
            lines.append("**Concerns:**")
            for concern in analysis["concerns"]:
                lines.append(f"- {concern}")
            lines.append("")
        
        # Memory blocks detail
        lines.extend([
            "### Memory Blocks",
            "",
        ])
        
        for block in mem.get("blocks", []):
            label = block.get("label") or "unnamed"
            value = block.get("value") or ""
            char_count = block.get("char_count", 0)
            usage = block.get("usage_pct", 0)
            desc = block.get("description") or "No description"
            
            lines.extend([
                f"#### `{label}` ({char_count:,} chars, {usage}% capacity)",
                "",
                f"*{desc}*",
                "",
            ])
            
            if char_count == 0:
                lines.append("*(empty)*")
            elif char_count > 2000:
                # Truncate long values
                lines.extend([
                    "```",
                    value[:2000] + f"\n\n... [truncated, {char_count - 2000} more chars]",
                    "```",
                ])
            else:
                lines.extend([
                    "```",
                    value,
                    "```",
                ])
            
            lines.append("")
        
        # Context (compiled memory)
        if mem.get("context"):
            context = mem["context"]
            context_len = mem.get("context_length", len(context))
            lines.extend([
                "### Compiled Context",
                "",
                f"*Total: {context_len:,} chars*",
                "",
            ])
            if context_len > 3000:
                lines.extend([
                    "```",
                    context[:3000] + f"\n\n... [truncated, {context_len - 3000} more chars]",
                    "```",
                ])
            else:
                lines.extend([
                    "```",
                    context,
                    "```",
                ])
            lines.append("")
        
        lines.extend(["---", ""])
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Fetch and analyze Letta agent memories")
    default_output = Path(__file__).resolve().parents[1] / "reports" / "letta_memory_report.md"
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=str(default_output),
        help="Output file path (default: letta/reports/letta_memory_report.md)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also output raw JSON data"
    )
    args = parser.parse_args()
    
    print("🧠 Letta Memory Fetcher")
    print("=" * 50)
    
    # Initialize client
    print("📡 Connecting to Letta...")
    try:
        client = AgenticLearning()
        print("✅ Connected")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)
    
    # Fetch agents
    print("\n🔍 Fetching agents...")
    agents = fetch_all_agents(client)
    print(f"   Found {len(agents)} Universal Agent-related agents")
    
    if not agents:
        print("⚠️ No agents found. The system may not have created any Letta agents yet.")
        # Try fetching the main agent directly
        print("   Trying direct fetch for 'universal_agent'...")
        agents = [{"name": "universal_agent", "id": None}]
    
    # Fetch memories for each agent
    print("\n📥 Fetching memories...")
    memories = []
    for agent in agents:
        agent_name = agent.get("name")
        if not agent_name:
            continue
        print(f"   → {agent_name}...")
        mem = fetch_agent_memory(client, agent_name)
        memories.append(mem)
        
        # Quick status
        block_count = len(mem.get("blocks", []))
        error = mem.get("error")
        if error:
            print(f"      ⚠️ {error}")
        else:
            print(f"      ✅ {block_count} blocks")
    
    # Generate report
    print("\n📝 Generating report...")
    report = generate_report(agents, memories)
    
    # Save report
    output_path = Path(args.output)
    output_path.write_text(report)
    print(f"✅ Report saved: {output_path}")
    
    # Optionally save JSON
    if args.json:
        json_path = output_path.with_suffix(".json")
        json_data = {
            "generated_at": datetime.now().isoformat(),
            "agents": agents,
            "memories": memories,
        }
        json_path.write_text(json.dumps(json_data, indent=2, default=str))
        print(f"✅ JSON saved: {json_path}")
    
    # Print quick summary
    print("\n" + "=" * 50)
    print("📊 Quick Summary:")
    for mem in memories:
        agent_name = mem.get("agent_name", "unknown")
        analysis = analyze_memory_usefulness(mem)
        print(f"   {analysis['recommendation'].split(' ')[0]} {agent_name}: "
              f"{analysis['non_empty_blocks']}/{analysis['total_blocks']} blocks, "
              f"{analysis['total_chars']:,} chars")
    
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
