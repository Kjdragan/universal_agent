"""
Test Letta sub-agent isolation from primary agent memory/history.
Run: uv run python tests/test_letta_subagent_isolation.py
"""

import os
import sys
import time
from dotenv import load_dotenv

load_dotenv("/home/kjdragan/lrepos/universal_agent/.env")

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import sitecustomize  # noqa: F401

from agentic_learning import AgenticLearning

PRIMARY_AGENT = "test_primary_agent"
SUBAGENT_TYPE = "report-creation-expert"
SUBAGENT_AGENT = f"{PRIMARY_AGENT} {SUBAGENT_TYPE}"
MEMORY_BLOCKS = ["human", "project_context"]


def ensure_agent(client: AgenticLearning, name: str) -> bool:
    try:
        agent = client.agents.retrieve(name)
        if agent:
            return True
    except Exception:
        pass

    try:
        client.agents.create(
            agent=name,
            memory=MEMORY_BLOCKS,
            model="anthropic/claude-sonnet-4-20250514",
        )
        return True
    except Exception as exc:
        print(f"❌ Failed to create agent '{name}': {exc}")
        return False


def main() -> None:
    print("=" * 60)
    print("LETTA SUB-AGENT ISOLATION TEST")
    print("=" * 60)

    client = AgenticLearning()

    # Clean up any previous runs
    try:
        client.agents.delete(PRIMARY_AGENT)
    except Exception:
        pass
    try:
        client.agents.delete(SUBAGENT_AGENT)
    except Exception:
        pass

    if not ensure_agent(client, PRIMARY_AGENT):
        return
    if not ensure_agent(client, SUBAGENT_AGENT):
        return

    tag = f"subagent-isolation-{int(time.time())}"
    print(f"Tag: {tag}")

    print("\n1. Upserting sub-agent memory only...")
    try:
        client.memory.upsert(
            agent=SUBAGENT_AGENT,
            label="project_context",
            value=f"Sub-agent only tag: {tag}",
        )
        print("   ✅ Sub-agent memory upserted")
    except Exception as exc:
        print(f"   ⚠️ Memory upsert issue: {exc}")

    print("\n2. Capturing sub-agent message only...")
    try:
        client.messages.capture(
            agent=SUBAGENT_AGENT,
            request_messages=[{"role": "user", "content": f"Sub-agent tag {tag}"}],
            response_dict={"role": "assistant", "content": f"Ack {tag}"},
            model="claude",
            provider="claude",
        )
        print("   ✅ Sub-agent message captured")
    except Exception as exc:
        print(f"   ❌ Capture failed: {exc}")

    print("\nWaiting 6 seconds for Letta sleeptime...")
    time.sleep(6)

    print("\n3. Checking message history isolation...")
    sub_messages = client.messages.list(agent=SUBAGENT_AGENT)
    primary_messages = client.messages.list(agent=PRIMARY_AGENT)

    sub_has_tag = any(tag in str(getattr(msg, "content", msg)) for msg in sub_messages)
    primary_has_tag = any(tag in str(getattr(msg, "content", msg)) for msg in primary_messages)

    print(f"   Sub-agent messages contain tag: {sub_has_tag}")
    print(f"   Primary agent messages contain tag: {primary_has_tag}")

    print("\n4. Checking memory context isolation (best-effort)...")
    sub_context = client.memory.context.retrieve(agent=SUBAGENT_AGENT) or ""
    primary_context = client.memory.context.retrieve(agent=PRIMARY_AGENT) or ""

    print(f"   Sub-agent context has tag: {tag in sub_context}")
    print(f"   Primary agent context has tag: {tag in primary_context}")

    print("\n" + "=" * 60)
    print("RESULT SUMMARY")
    print("=" * 60)
    if sub_has_tag and not primary_has_tag:
        print("✅ PASS: Sub-agent data is isolated from primary agent")
    else:
        print("⚠️ CHECK: Isolation failed or messages not captured")
    print("=" * 60)


if __name__ == "__main__":
    main()
