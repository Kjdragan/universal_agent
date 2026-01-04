"""Runtime monkey patches for local dev/test runs."""

from __future__ import annotations

import os


def _patch_letta_memory_upsert() -> None:
    """Fix agentic_learning MemoryClient.upsert UnboundLocalError."""
    if os.getenv("UA_LETTA_UPSERT_PATCH", "1").lower() in {"0", "false", "no"}:
        return

    try:
        from agentic_learning.client.memory.client import MemoryClient
        from agentic_learning.client.utils import memory_placeholder
    except Exception:
        return

    def _fixed_upsert(self, agent: str, label: str, value: str = "", description: str = ""):
        agent_obj = self._parent.agents.retrieve(agent=agent)
        if not agent_obj:
            return None

        blocks = [b for b in agent_obj.memory.blocks if b.label == label]
        if not blocks:
            return self.create(agent=agent_obj, label=label, value=value, description=description)

        block = blocks[0]
        return self._letta.blocks.update(
            block_id=block.id,
            value=value or memory_placeholder(label),
            description=description,
        )

    if MemoryClient.upsert is not _fixed_upsert:
        MemoryClient.upsert = _fixed_upsert


_patch_letta_memory_upsert()
