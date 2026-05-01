"""Concrete node handlers for the DAG Runner.

Each handler is a factory function that returns an async callable matching
the DagRunner handler signature: ``async (node, state) -> result_dict``.

Handlers:
- ``make_subprocess_handler`` — runs shell commands via asyncio subprocess.
- ``make_llm_binary_classifier_handler`` — sends a prompt to an LLM and
  parses a strict ``true``/``false`` result for deterministic edge routing.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict

from universal_agent.services.dag_runner import DagState, STATUS_FAILED, STATUS_SUCCESS

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Subprocess handler
# --------------------------------------------------------------------------- #

def make_subprocess_handler(
    *,
    workspace_root: Path,
) -> Callable[[Dict[str, Any], DagState], Awaitable[Dict[str, Any]]]:
    """Return a handler that runs shell commands in *workspace_root*."""

    async def _handler(node: Dict[str, Any], state: DagState) -> Dict[str, Any]:
        command = str(node.get("command") or "").strip()
        if not command:
            return {
                "status": STATUS_FAILED,
                "error": f"Node '{node['id']}' has no 'command' field.",
            }

        logger.info("DAG subprocess: node=%s cmd='%s' cwd=%s", node["id"], command, workspace_root)

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0

        logger.info(
            "DAG subprocess finished: node=%s exit_code=%d stdout_len=%d",
            node["id"],
            exit_code,
            len(stdout),
        )

        result: Dict[str, Any] = {
            "context_update": {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "last_command": command,
            },
        }

        if exit_code != 0:
            result["status"] = STATUS_FAILED
            result["error"] = (
                f"Command exited with code {exit_code}: {stderr[:500]}"
            )
        else:
            result["status"] = STATUS_SUCCESS

        return result

    return _handler


# Convenience alias for backwards compat / direct use
subprocess_handler = make_subprocess_handler


# --------------------------------------------------------------------------- #
# LLM binary classifier handler
# --------------------------------------------------------------------------- #

def make_llm_binary_classifier_handler(
    *,
    llm_call: Callable[[str], Awaitable[str]],
) -> Callable[[Dict[str, Any], DagState], Awaitable[Dict[str, Any]]]:
    """Return a handler that classifies context via binary LLM judgment.

    Args:
        llm_call: An async function ``async (prompt_text) -> response_text``
            that sends a prompt to ZAI/Claude and returns the raw text response.
    """

    async def _handler(node: Dict[str, Any], state: DagState) -> Dict[str, Any]:
        prompt = str(node.get("prompt") or "").strip()
        if not prompt:
            return {
                "status": STATUS_FAILED,
                "error": f"Node '{node['id']}' has no 'prompt' field.",
            }

        # Build context-aware prompt
        context_snippet = ""
        if state.context.get("stdout"):
            context_snippet = f"\n\n--- Recent Output ---\n{state.context['stdout'][-2000:]}"

        full_prompt = (
            "You are a strict binary classifier. You MUST answer with ONLY "
            "the word 'true' or 'false'. No explanation, no caveats.\n\n"
            f"Question: {prompt}"
            f"{context_snippet}"
        )

        logger.info("DAG binary classifier: node=%s prompt='%s'", node["id"], prompt[:100])

        try:
            raw_response = await llm_call(full_prompt)
        except Exception as exc:
            logger.error("LLM call failed for node '%s': %s", node["id"], exc)
            return {
                "status": STATUS_FAILED,
                "error": f"LLM call failed: {exc}",
            }

        # Parse response — strict binary
        cleaned = str(raw_response).strip().lower()
        if cleaned.startswith("true"):
            binary_result = "true"
        elif cleaned.startswith("false"):
            binary_result = "false"
        else:
            # Ambiguous response defaults to "false" for safety / conservatism
            logger.warning(
                "DAG binary classifier got ambiguous response '%s' for node '%s', "
                "defaulting to 'false'",
                cleaned[:100],
                node["id"],
            )
            binary_result = "false"

        logger.info(
            "DAG binary classifier result: node=%s result=%s raw='%s'",
            node["id"],
            binary_result,
            cleaned[:50],
        )

        return {
            "status": STATUS_SUCCESS,
            "result": binary_result,
            "context_update": {
                "classifier_raw": cleaned,
                "classifier_result": binary_result,
            },
        }

    return _handler
