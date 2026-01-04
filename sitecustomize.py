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


def _patch_letta_context_fallback() -> None:
    """Ensure Letta context is available to background tasks."""
    if os.getenv("UA_LETTA_CONTEXT_FALLBACK", "1").lower() in {"0", "false", "no"}:
        return

    try:
        import agentic_learning.core as core
    except Exception:
        return

    if getattr(core, "_UA_LETTA_FALLBACK_PATCHED", False):
        return

    core._UA_LETTA_FALLBACK_PATCHED = True
    core._UA_LETTA_FALLBACK_CONFIG = None

    original_get_current_config = core.get_current_config

    def _get_current_config_fallback():
        cfg = original_get_current_config()
        if cfg is not None:
            return cfg
        return getattr(core, "_UA_LETTA_FALLBACK_CONFIG", None)

    core.get_current_config = _get_current_config_fallback

    original_enter = core.LearningContext.__enter__
    original_aenter = core.LearningContext.__aenter__

    def _enter_with_fallback(self):
        ctx = original_enter(self)
        core._UA_LETTA_FALLBACK_CONFIG = original_get_current_config()
        return ctx

    async def _aenter_with_fallback(self):
        ctx = await original_aenter(self)
        core._UA_LETTA_FALLBACK_CONFIG = original_get_current_config()
        return ctx

    core.LearningContext.__enter__ = _enter_with_fallback
    core.LearningContext.__aenter__ = _aenter_with_fallback


_patch_letta_context_fallback()


def _patch_letta_claude_stream_flush() -> None:
    """Flush Claude SDK capture on ResultMessage instead of connection close."""
    if os.getenv("UA_LETTA_CLAUDE_STREAM_PATCH", "1").lower() in {"0", "false", "no"}:
        return

    try:
        from agentic_learning.interceptors.claude import ClaudeInterceptor
    except Exception:
        return

    if getattr(ClaudeInterceptor, "_ua_stream_patch", False):
        return

    ClaudeInterceptor._ua_stream_patch = True

    async def _wrap_message_iterator(self, original_iterator, config):
        accumulated_text = []

        async def _flush_capture():
            user_message = config.get("pending_user_message")
            assistant_message = "".join(accumulated_text) if accumulated_text else None
            if user_message or assistant_message:
                from agentic_learning.interceptors.utils import _save_conversation_turn_async

                await _save_conversation_turn_async(
                    provider=self.PROVIDER,
                    model="claude",
                    request_messages=self.build_request_messages(user_message) if user_message else [],
                    response_dict={"role": "assistant", "content": assistant_message} if assistant_message else {"role": "assistant", "content": ""},
                    register_task=True,
                )
            config["pending_user_message"] = None
            accumulated_text.clear()

        try:
            async for message in original_iterator:
                msg_type = message.get("type", "unknown")

                if msg_type == "assistant":
                    assistant_message = message.get("message", {})
                    content_blocks = assistant_message.get("content", [])
                    for block in content_blocks:
                        if block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                accumulated_text.append(text)

                yield message

                # Flush after result message to persist this turn.
                if msg_type == "result":
                    await _flush_capture()
        finally:
            if accumulated_text or config.get("pending_user_message"):
                await _flush_capture()

    ClaudeInterceptor._wrap_message_iterator = _wrap_message_iterator


_patch_letta_claude_stream_flush()
