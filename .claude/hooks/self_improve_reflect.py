#!/usr/bin/env python3
"""Detached session reflector for the self-improvement Stop hook.

Reads a Claude Code Stop-hook payload, decides (throttled) whether enough new
work has happened to be worth reflecting on, and if so asks a cheap model to
propose DURABLE CLAUDE.md improvements based on what actually happened this
session. Proposals are written to .claude/self-improvement/proposals/<date>.md
for human review. This script NEVER edits CLAUDE.md.

Invoked detached by self-improve-stop.sh with one argv: the path to a temp file
containing the hook payload JSON. Runs with UA_SELFIMPROVE_REFLECT=1 so the
`claude -p` child it spawns short-circuits the Stop hook (no recursion).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import sys
import tempfile

# --- Tunables (env-overridable) ------------------------------------------------
MIN_USER_TURNS = int(os.environ.get("UA_SELFIMPROVE_MIN_TURNS", "4"))
STRIDE = int(os.environ.get("UA_SELFIMPROVE_STRIDE", "8"))
MODEL = os.environ.get("UA_SELFIMPROVE_MODEL", "haiku")
MAX_CHARS = int(os.environ.get("UA_SELFIMPROVE_MAX_CHARS", "24000"))
TIMEOUT_S = int(os.environ.get("UA_SELFIMPROVE_TIMEOUT", "180"))
DRYRUN = os.environ.get("UA_SELFIMPROVE_DRYRUN") == "1"

PROMPT_TEMPLATE = """You are reviewing a single Claude Code working session in the `universal_agent` repository. Your job: decide whether this session surfaced any DURABLE, GENERALIZABLE lesson worth adding to CLAUDE.md (the agent operating manual that future sessions read).

GOOD candidates (propose these):
- A gotcha or footgun discovered (a path that was wrong, a command that failed for a non-obvious reason, a tool quirk).
- A convention or expectation that was clarified or corrected by the user.
- A repeated mistake the assistant made that a one-line rule would prevent.
- A non-obvious architectural fact that was learned the hard way.

BAD candidates (DO NOT propose — output NONE for these):
- One-off task facts, specific values, ticket/PR numbers, or anything session-specific (those belong in memory, not CLAUDE.md).
- Things CLAUDE.md already says (an excerpt is provided below — do not duplicate it).
- Vague advice ("be careful", "test more") with no concrete, checkable instruction.
- Anything you are not confident generalizes to future sessions.

Be conservative. Most sessions yield NOTHING worth adding — that is the expected, correct outcome. Only propose when there is a concrete, generalizable lesson.

=== CURRENT CLAUDE.md (head, for de-duplication) ===
{claude_md_head}

=== SESSION TRANSCRIPT (condensed) ===
{convo}

=== OUTPUT FORMAT ===
If nothing qualifies, respond with exactly:
NONE

Otherwise, respond with 1-3 proposals, each as:

### Proposal: <short title>
**Section of CLAUDE.md:** <which heading it belongs under, or "new section">
**Add this text:**
> <the exact prose/rule to add — concrete and self-contained>
**Why (evidence from this session):** <1-2 sentences citing what happened>

Respond with ONLY the proposals or the single word NONE. Do not use any tools. Do not take any action.
"""


def load_payload(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def iter_records(path: str):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def text_from_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text", ""))
        return "\n".join(out)
    return ""


def is_human_turn(rec: dict) -> bool:
    """A real user-typed turn: content is a non-empty string, or a block list
    containing text and no tool_result (tool-result user records are not human)."""
    if rec.get("type") != "user":
        return False
    msg = rec.get("message") or {}
    c = msg.get("content")
    if isinstance(c, str):
        return bool(c.strip())
    if isinstance(c, list):
        types = {b.get("type") for b in c if isinstance(b, dict)}
        return "text" in types and "tool_result" not in types
    return False


def main() -> None:
    if len(sys.argv) < 2:
        return
    payload = load_payload(sys.argv[1])
    if not payload or payload.get("stop_hook_active"):
        return

    transcript = payload.get("transcript_path")
    session_id = payload.get("session_id") or "unknown"
    cwd = payload.get("cwd") or os.getcwd()
    if not transcript or not os.path.exists(transcript):
        return

    recs = list(iter_records(transcript))
    human_turns = sum(1 for r in recs if is_human_turn(r))
    if human_turns < MIN_USER_TURNS:
        return

    base = os.path.join(cwd, ".claude", "self-improvement")
    state_dir = os.path.join(base, "state")
    prop_dir = os.path.join(base, "proposals")
    os.makedirs(state_dir, exist_ok=True)
    os.makedirs(prop_dir, exist_ok=True)

    state_file = os.path.join(state_dir, f"{session_id}.json")
    state = {"last_reflected_turns": 0, "seen_hashes": []}
    if os.path.exists(state_file):
        try:
            state.update(json.load(open(state_file)))
        except Exception:
            pass

    if human_turns - int(state.get("last_reflected_turns", 0)) < STRIDE:
        return

    # Condense the transcript to user prompts + assistant narration.
    parts = []
    for r in recs:
        t = r.get("type")
        msg = r.get("message") or {}
        if t == "user" and is_human_turn(r):
            parts.append("USER: " + text_from_content(msg.get("content")).strip())
        elif t == "assistant":
            txt = text_from_content(msg.get("content")).strip()
            if txt:
                parts.append("ASSISTANT: " + txt)
    convo = "\n\n".join(parts).strip()
    if not convo:
        return
    if len(convo) > MAX_CHARS:
        head = MAX_CHARS // 3
        tail = MAX_CHARS - head
        convo = convo[:head] + "\n\n...[middle elided]...\n\n" + convo[-tail:]

    claude_md = os.path.join(cwd, "CLAUDE.md")
    existing = ""
    if os.path.exists(claude_md):
        try:
            existing = open(claude_md).read()[:8000]
        except Exception:
            existing = ""

    prompt = PROMPT_TEMPLATE.format(claude_md_head=existing, convo=convo)

    # Always advance the throttle marker so we don't re-run on the same content.
    state["last_reflected_turns"] = human_turns

    if DRYRUN:
        print(f"[dryrun] human_turns={human_turns} convo_chars={len(convo)} "
              f"prompt_chars={len(prompt)} model={MODEL}")
        json.dump(state, open(state_file, "w"))
        return

    # Run the cheap reflection model from a scratch cwd so it loads no project
    # settings/MCP/skills (faster, and a second layer of recursion safety).
    scratch = tempfile.mkdtemp(prefix="ua-selfimprove-claude-")
    try:
        res = subprocess.run(
            ["claude", "-p", "--model", MODEL],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            cwd=scratch,
            env={**os.environ, "UA_SELFIMPROVE_REFLECT": "1"},
        )
    except Exception:
        json.dump(state, open(state_file, "w"))
        return
    finally:
        try:
            os.rmdir(scratch)
        except OSError:
            pass

    out = (res.stdout or "").strip()
    if not out or out.strip().upper() == "NONE" or out.strip().upper().startswith("NONE\n"):
        json.dump(state, open(state_file, "w"))
        return

    # De-dup: skip if we've already emitted this exact proposal text this session.
    h = hashlib.sha256(out.encode("utf-8")).hexdigest()[:16]
    seen = set(state.get("seen_hashes", []))
    if h in seen:
        json.dump(state, open(state_file, "w"))
        return
    seen.add(h)
    state["seen_hashes"] = sorted(seen)

    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M %Z").strip()
    day = now.strftime("%Y-%m-%d")

    prop_file = os.path.join(prop_dir, f"{day}.md")
    new = not os.path.exists(prop_file)
    with open(prop_file, "a") as f:
        if new:
            f.write(f"# CLAUDE.md improvement proposals — {day}\n\n")
            f.write("_Auto-drafted by the self-improvement Stop hook. Review, then "
                    "accept into CLAUDE.md or delete. Never auto-applied._\n\n")
        f.write(f"---\n\n## {stamp} · session `{session_id[:8]}` "
                f"({human_turns} turns)\n\n")
        f.write(out.strip() + "\n\n")

    json.dump(state, open(state_file, "w"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Self-improvement must never crash a session's teardown.
        pass
