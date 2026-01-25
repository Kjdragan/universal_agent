"""
List Letta agents matching 'universal_agent*' with basic metadata.
Run: uv run python letta/scripts/list_agents.py
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

from agentic_learning import AgenticLearning

PREFIX = "universal_agent"


def main() -> None:
    client = AgenticLearning()
    agents = client.agents.list()

    print(f"Total agents: {len(agents)}")
    print(f"Filtered by prefix: {PREFIX}")

    matching = [agent for agent in agents if getattr(agent, "name", "").startswith(PREFIX)]
    if not matching:
        print("No matching agents found.")
        return

    for agent in matching:
        name = getattr(agent, "name", "<unknown>")
        blocks = getattr(agent, "memory", None)
        labels = []
        if blocks and getattr(blocks, "blocks", None):
            labels = [block.label for block in blocks.blocks if getattr(block, "label", None)]
        messages = []
        try:
            messages = client.messages.list(agent=name)
            msg_count = len(messages)
        except Exception as exc:
            msg_count = f"error: {exc}"

        print("-")
        print(f"Agent: {name}")
        print(f"Memory blocks: {', '.join(labels) if labels else 'none'}")
        print(f"Messages: {msg_count}")
        if isinstance(msg_count, int) and messages:
            print("Latest messages:")
            for msg in messages[:3]:
                role = getattr(msg, "role", "unknown")
                content = str(getattr(msg, "content", msg)).replace("\n", " ")
                snippet = content[:160] + ("..." if len(content) > 160 else "")
                print(f"  - [{role}] {snippet}")


if __name__ == "__main__":
    main()
