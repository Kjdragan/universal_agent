"""
Test Letta Learning SDK sub-agent capture and memory context.
Run: uv run python tests/test_letta_subagent.py
"""

import os
import sys
import time
from dotenv import load_dotenv

load_dotenv()

repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

import sitecustomize  # noqa: F401

from agentic_learning import AgenticLearning

SUBAGENT_TYPE = "report-creation-expert"
SUBAGENT_NAME = f"universal_agent {SUBAGENT_TYPE}"
MEMORY_BLOCKS = ["human", "system_rules", "project_context"]


def ensure_subagent(client: AgenticLearning) -> bool:
    print("\n1. Ensuring sub-agent exists...")
    try:
        agent = client.agents.retrieve(SUBAGENT_NAME)
        if agent:
            print(f"   ✅ Sub-agent exists: {agent.name}")
            return True
    except Exception as exc:
        print(f"   ⚠️ Sub-agent retrieve failed: {exc}")

    try:
        agent = client.agents.create(
            agent=SUBAGENT_NAME,
            memory=MEMORY_BLOCKS,
            model="anthropic/claude-sonnet-4-20250514",
        )
        print(f"   ✅ Sub-agent created: {agent.name}")
        return True
    except Exception as exc:
        print(f"   ❌ Sub-agent creation failed: {exc}")
        return False


def seed_memory(client: AgenticLearning, seed_tag: str) -> None:
    print("\n2. Seeding a memory block for context retrieval...")
    try:
        client.memory.upsert(
            agent=SUBAGENT_NAME,
            label="project_context",
            value=f"Sub-agent seed memory tag: {seed_tag}",
        )
        print("   ✅ Memory upsert succeeded")
    except Exception as exc:
        print(f"   ⚠️ Memory upsert issue: {exc}")


def capture_subagent_message(client: AgenticLearning, run_tag: str) -> bool:
    print("\n3. Capturing a sub-agent conversation...")
    prompt = (
        f"[Subagent: {SUBAGENT_TYPE}] Generate a short report summary. "
        f"Tag: {run_tag}"
    )
    response = f"Summary generated for tag {run_tag}."
    try:
        client.messages.capture(
            agent=SUBAGENT_NAME,
            request_messages=[{"role": "user", "content": prompt}],
            response_dict={"role": "assistant", "content": response},
            model="claude",
            provider="claude",
        )
        print("   ✅ Capture succeeded")
        return True
    except Exception as exc:
        print(f"   ❌ Capture failed: {exc}")
        return False


def verify_messages(client: AgenticLearning, run_tag: str) -> bool:
    print("\n4. Verifying message history...")
    try:
        messages = client.messages.list(agent=SUBAGENT_NAME)
    except Exception as exc:
        print(f"   ❌ Failed to list messages: {exc}")
        return False

    found = False
    for msg in messages:
        content = str(getattr(msg, "content", msg))
        if run_tag in content:
            found = True
            break

    if found:
        print("   ✅ Found captured message in sub-agent history")
        return True

    print("   ⚠️ Did not find the captured message in history")
    return False


def check_context(client: AgenticLearning, seed_tag: str) -> None:
    print("\n5. Checking memory context retrieval...")
    try:
        context = client.memory.context.retrieve(agent=SUBAGENT_NAME) or ""
    except Exception as exc:
        print(f"   ❌ Failed to retrieve context: {exc}")
        return

    if not context:
        print("   ⚠️ Memory context is empty")
        return

    preview = context.replace("\n", " ")[:200]
    print(f"   ✅ Context retrieved (preview): {preview}...")
    if seed_tag in context:
        print("   ✅ Seed tag present in context")
    else:
        print("   ⚠️ Seed tag not found in context (sleeptime may still be processing)")


def main() -> None:
    print("=" * 60)
    print("LETTA SUB-AGENT MEMORY TEST")
    print("=" * 60)

    client = AgenticLearning()
    if not ensure_subagent(client):
        return

    seed_tag = f"subagent-seed-{int(time.time())}"
    run_tag = f"subagent-run-{int(time.time())}"

    seed_memory(client, seed_tag)
    capture_ok = capture_subagent_message(client, run_tag)

    print("\nWaiting 6 seconds for Letta processing...")
    time.sleep(6)

    history_ok = verify_messages(client, run_tag)
    check_context(client, seed_tag)

    print("\n" + "=" * 60)
    print("RESULT SUMMARY")
    print("=" * 60)
    print(f"Capture: {'✅ PASS' if capture_ok else '❌ FAIL'}")
    print(f"History: {'✅ PASS' if history_ok else '❌ FAIL'}")
    print("=" * 60)


if __name__ == "__main__":
    main()
