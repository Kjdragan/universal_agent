#!/usr/bin/env python3
"""
Session continuity probe for Claude Agent SDK.

Tests whether resume/fork preserves server-side session state without
re-supplying local transcript.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from dataclasses import dataclass
from typing import Optional

import anyio

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock


DEFAULT_STATE_PATH = os.path.join(
    "AGENT_RUN_WORKSPACES", "session_continuity_probe_state.json"
)


@dataclass
class ProbeResult:
    response_text: str
    session_id: Optional[str]


def _expand_env_vars(value: str) -> str:
    if "${" not in value:
        return value
    expanded = value
    for key, val in os.environ.items():
        expanded = expanded.replace(f"${{{key}}}", val)
    return expanded


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"").strip("'")
            if key and key not in os.environ:
                os.environ[key] = _expand_env_vars(value)


def _load_state(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"State file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_state(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


async def _run_query(client: ClaudeSDKClient, prompt: str) -> ProbeResult:
    await client.query(prompt)
    text_parts: list[str] = []
    session_id: Optional[str] = None
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(msg, ResultMessage):
            session_id = msg.session_id
    return ProbeResult(response_text="".join(text_parts).strip(), session_id=session_id)


async def _run_session(options: ClaudeAgentOptions, prompt: str) -> ProbeResult:
    async with ClaudeSDKClient(options=options) as client:
        return await _run_query(client, prompt)


def _expect_contains(text: str, needle: str) -> bool:
    return needle in text.replace("\n", " ")


async def run_initial(args: argparse.Namespace) -> int:
    nonce = str(uuid.uuid4())
    ack_prompt = (
        f"Remember this nonce: NONCE={nonce}. Reply only with 'ACK {nonce}'."
    )
    recall_prompt = "What nonce did I ask you to remember?"

    options = ClaudeAgentOptions(model=args.model)

    async with ClaudeSDKClient(options=options) as client:
        ack = await _run_query(client, ack_prompt)
        recall = await _run_query(client, recall_prompt)

    ack_ok = _expect_contains(ack.response_text, f"ACK {nonce}")
    recall_ok = _expect_contains(recall.response_text, nonce)

    state = {
        "nonce": nonce,
        "session_id": recall.session_id or ack.session_id,
        "ack_response": ack.response_text,
        "recall_response": recall.response_text,
    }
    _save_state(args.state_path, state)

    print("Initial run results:")
    print(f"- ACK response: {ack.response_text}")
    print(f"- Recall response: {recall.response_text}")
    print(f"- Session ID: {state['session_id']}")
    print(f"- ACK PASS: {ack_ok}")
    print(f"- Recall PASS: {recall_ok}")

    return 0 if ack_ok and recall_ok else 1


async def run_resume(args: argparse.Namespace) -> int:
    state = _load_state(args.state_path)
    session_id = state.get("session_id")
    nonce = state.get("nonce")
    if not session_id or not nonce:
        raise ValueError("State file missing session_id or nonce.")

    if args.clear_session_id:
        print("Negative control: clearing session_id before resume.")
        session_id = None

    options = ClaudeAgentOptions(
        model=args.model,
        continue_conversation=bool(session_id),
        resume=session_id,
        fork_session=args.fork_session if session_id else False,
    )
    recall_prompt = "What nonce did I ask you to remember?"
    recall = await _run_session(options, recall_prompt)

    recall_ok = _expect_contains(recall.response_text, nonce)

    print("Resume run results:")
    print(f"- Resume session: {session_id}")
    print(f"- Fork session: {args.fork_session}")
    print(f"- Response: {recall.response_text}")
    print(f"- Returned session ID: {recall.session_id}")
    print(f"- Recall PASS: {recall_ok}")

    return 0 if recall_ok else 1


async def run_fork_test(args: argparse.Namespace) -> int:
    state = _load_state(args.state_path)
    base_session_id = state.get("session_id")
    if not base_session_id:
        raise ValueError("State file missing session_id.")

    # Branch A: continue without fork.
    branch_a_opts = ClaudeAgentOptions(
        model=args.model, continue_conversation=True, resume=base_session_id
    )
    branch_a_pref = "Preference A"
    await _run_session(branch_a_opts, f"Remember this preference: {branch_a_pref}.")
    branch_a_check = await _run_session(
        ClaudeAgentOptions(model=args.model, continue_conversation=True, resume=base_session_id),
        "What preference did I ask you to remember?",
    )
    branch_a_ok = _expect_contains(branch_a_check.response_text, branch_a_pref)

    # Branch B: fork from base.
    branch_b_opts = ClaudeAgentOptions(
        model=args.model,
        continue_conversation=True,
        resume=base_session_id,
        fork_session=True,
    )
    branch_b_pref = "Preference B"
    branch_b_set = await _run_session(
        branch_b_opts, f"Remember this preference: {branch_b_pref}."
    )
    branch_b_session = branch_b_set.session_id
    branch_b_check = await _run_session(
        ClaudeAgentOptions(
            model=args.model,
            continue_conversation=True,
            resume=branch_b_session or base_session_id,
        ),
        "What preference did I ask you to remember?",
    )
    branch_b_ok = _expect_contains(branch_b_check.response_text, branch_b_pref)

    print("Fork test results:")
    print(f"- Base session: {base_session_id}")
    print(f"- Branch A OK: {branch_a_ok} | Response: {branch_a_check.response_text}")
    print(
        f"- Branch B session: {branch_b_session} | OK: {branch_b_ok} | Response: {branch_b_check.response_text}"
    )

    return 0 if branch_a_ok and branch_b_ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Session continuity probe")
    parser.add_argument(
        "--mode",
        choices=["initial", "resume", "fork"],
        required=True,
        help="Run mode: initial, resume, or fork",
    )
    parser.add_argument(
        "--state-path",
        default=DEFAULT_STATE_PATH,
        help="Path to save/load session state JSON",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model to use for the probe (defaults to env vars if set)",
    )
    parser.add_argument(
        "--fork-session",
        action="store_true",
        help="Enable fork_session when resuming",
    )
    parser.add_argument(
        "--clear-session-id",
        action="store_true",
        help="Negative control: omit session_id when resuming",
    )

    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    _load_env_file(os.path.join(repo_root, ".env"))

    if not args.model:
        args.model = (
            os.getenv("PROBE_MODEL")
            or os.getenv("MODEL_NAME")
            or os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL")
            or os.getenv("ANTHROPIC_DEFAULT_HAIKU_MODEL")
            or os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL")
        )

    if not args.model:
        raise SystemExit(
            "Model not set. Provide --model or set PROBE_MODEL/MODEL_NAME/ANTHROPIC_DEFAULT_SONNET_MODEL."
        )

    if args.mode == "initial":
        exit_code = anyio.run(run_initial, args)
    elif args.mode == "resume":
        exit_code = anyio.run(run_resume, args)
    else:
        exit_code = anyio.run(run_fork_test, args)

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
