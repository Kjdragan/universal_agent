import asyncio
import os
import re
import sqlite3
import sys
from typing import Any, AsyncIterator, Callable, Optional, TextIO

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style

from universal_agent.agent_core import AgentEvent, EventType


class DualWriter:
    """Writes to both a file and the original stream (stdout/stderr)."""

    def __init__(self, file_handle: TextIO, original_stream: TextIO):
        self.file = file_handle
        self.stream = original_stream

    def write(self, data: str) -> None:
        self.stream.write(data)
        self.stream.flush()
        self.file.write(data)
        self.file.flush()

    def flush(self) -> None:
        self.stream.flush()
        self.file.flush()

    def isatty(self) -> bool:
        """Check if the stream is a TTY (needed by prompt_toolkit)."""
        return hasattr(self.stream, "isatty") and self.stream.isatty()

    def fileno(self) -> int:
        """Return file descriptor (needed by prompt_toolkit)."""
        return self.stream.fileno()


def open_run_log(workspace_dir: str) -> TextIO:
    run_log_path = os.path.join(workspace_dir, "run.log")
    return open(run_log_path, "a", encoding="utf-8")


def attach_run_log(log_file: TextIO) -> None:
    sys.stdout = DualWriter(log_file, sys.stdout)
    sys.stderr = DualWriter(log_file, sys.stderr)


def read_run_log_tail(workspace_dir: str, tail_lines: int = 100) -> str:
    if not workspace_dir:
        return ""

    run_log_path = os.path.join(workspace_dir, "run.log")
    if not os.path.exists(run_log_path):
        return ""

    try:
        with open(run_log_path, "r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()
        tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
        return "".join(tail)
    except Exception as exc:
        print(f"⚠️ Fallback log read failed: {exc}")
        return ""


_LOCAL_TRACE_ID_PATTERN = re.compile(r"\[local-toolkit-trace-id: ([0-9a-f]{32})\]")


def collect_local_tool_trace_ids(workspace_dir: str) -> list[str]:
    if not workspace_dir:
        return []
    run_log_path = os.path.join(workspace_dir, "run.log")
    if not os.path.exists(run_log_path):
        return []
    trace_ids: set[str] = set()
    try:
        with open(run_log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                match = _LOCAL_TRACE_ID_PATTERN.search(line)
                if match:
                    trace_ids.add(match.group(1))
    except Exception:
        return []
    return sorted(trace_ids)


def summarize_response(text: str, max_chars: int = 700) -> str:
    if not text:
        return ""
    compact = " ".join(text.split())
    if len(compact) > max_chars:
        return compact[: max_chars - 3] + "..."
    return compact


def build_prompt_session(history_file: str) -> Optional[PromptSession]:
    # Only use PromptSession if running in an interactive terminal
    if not sys.stdin.isatty():
        return None

    prompt_style = Style.from_dict(
        {
            "prompt": "#00aa00 bold",  # Green prompt
        }
    )

    return PromptSession(
        history=FileHistory(history_file),
        auto_suggest=AutoSuggestFromHistory(),
        multiline=False,  # Single line, but with full editing support
        style=prompt_style,
        enable_history_search=True,  # Ctrl+R for history search
    )


async def read_prompt_input(
    prompt_session: Optional[PromptSession],
    prompt_text: str,
) -> str:
    if prompt_session:
        with patch_stdout():
            return await prompt_session.prompt_async(prompt_text)

    # Non-interactive mode: read from stdin directly
    try:
        user_input = await asyncio.get_event_loop().run_in_executor(
            None, sys.stdin.readline
        )
        if not user_input:  # EOF
            raise EOFError
        return user_input
    except Exception:
        raise EOFError


async def render_agent_events(
    event_stream: AsyncIterator[AgentEvent],
) -> dict[str, Any]:
    response_text = ""
    tool_calls = 0
    tool_results = 0
    work_products: list[dict[str, Any]] = []
    status_updates: list[dict[str, Any]] = []
    errors: list[str] = []
    auth_required = False
    auth_link = None

    async for event in event_stream:
        if event.type == EventType.TEXT:
            text = event.data.get("text", "")
            response_text += text
            if text:
                print(text, end="", flush=True)
        elif event.type == EventType.TOOL_CALL:
            tool_calls += 1
        elif event.type == EventType.TOOL_RESULT:
            tool_results += 1
        elif event.type == EventType.AUTH_REQUIRED:
            auth_required = True
            auth_link = event.data.get("auth_link")
        elif event.type == EventType.WORK_PRODUCT:
            work_products.append(event.data or {})
        elif event.type == EventType.STATUS:
            status_updates.append(event.data or {})
        elif event.type == EventType.ERROR:
            error_text = (event.data or {}).get("error", "")
            if error_text:
                errors.append(error_text)

    if response_text and not response_text.endswith("\n"):
        print()

    return {
        "response_text": response_text,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "work_products": work_products,
        "status_updates": status_updates,
        "errors": errors,
        "auth_required": auth_required,
        "auth_link": auth_link,
    }


def list_workspace_artifacts(workspace_dir: str) -> list[str]:
    if not workspace_dir or not os.path.isdir(workspace_dir):
        return []
    artifacts = []
    for name in sorted(os.listdir(workspace_dir)):
        if name.lower().endswith((".html", ".pdf", ".pptx")):
            artifacts.append(name)
    return artifacts


def print_job_completion_summary(
    conn: sqlite3.Connection,
    run_id: str,
    status: str,
    workspace_dir: str,
    response_text: str,
    trace: Optional[dict] = None,
    update_restart_file_cb: Optional[
        Callable[[str, str, Optional[str], Optional[str], Optional[str], Optional[str]], None]
    ] = None,
) -> None:
    local_trace_ids = collect_local_tool_trace_ids(workspace_dir)
    main_trace_id = None
    if isinstance(trace, dict):
        main_trace_id = trace.get("trace_id")
    artifacts = list_workspace_artifacts(workspace_dir)
    receipt_rows = conn.execute(
        """
        SELECT tool_name, status, idempotency_key, response_ref
        FROM tool_calls
        WHERE run_id = ? AND status = 'succeeded' AND side_effect_class != 'read_only'
        ORDER BY updated_at DESC
        LIMIT 5
        """,
        (run_id,),
    ).fetchall()
    receipts = []
    for row in receipt_rows:
        response_preview = (row["response_ref"] or "")[:200]
        receipts.append(
            {
                "tool_name": row["tool_name"],
                "status": row["status"],
                "idempotency_key": row["idempotency_key"],
                "response_ref": response_preview,
            }
        )
    evidence_rows = conn.execute(
        """
        SELECT tool_name, response_ref
        FROM tool_calls
        WHERE run_id = ? AND status = 'succeeded' AND side_effect_class != 'read_only'
        ORDER BY updated_at DESC
        """,
        (run_id,),
    ).fetchall()
    evidence_receipts = [
        {
            "tool_name": row["tool_name"],
            "response_ref": row["response_ref"] or "",
        }
        for row in evidence_rows
    ]
    replay_rows = conn.execute(
        """
        SELECT tool_name, replay_status
        FROM tool_calls
        WHERE run_id = ? AND replay_status IS NOT NULL
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        (run_id,),
    ).fetchall()
    replayed = [
        {"tool_name": row["tool_name"], "replay_status": row["replay_status"]}
        for row in replay_rows
    ]

    abandoned_rows = conn.execute(
        """
        SELECT tool_name, error_detail
        FROM tool_calls
        WHERE run_id = ? AND status = 'abandoned_on_resume'
        ORDER BY updated_at DESC
        LIMIT 10
        """,
        (run_id,),
    ).fetchall()
    abandoned = []
    for row in abandoned_rows:
        detail = (row["error_detail"] or "").lower()
        outcome = "relaunched"
        if "needs_human" in detail or "failed" in detail:
            outcome = "needs-human"
        abandoned.append({"tool_name": row["tool_name"], "outcome": outcome})

    summary_row = conn.execute(
        """
        SELECT
            COUNT(*) AS total_tool_calls,
            SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
            SUM(CASE WHEN status = 'prepared' THEN 1 ELSE 0 END) AS prepared,
            SUM(CASE WHEN status = 'abandoned_on_resume' THEN 1 ELSE 0 END) AS abandoned,
            SUM(CASE WHEN replay_status IS NOT NULL THEN 1 ELSE 0 END) AS replayed
        FROM tool_calls
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    tool_counts = conn.execute(
        """
        SELECT tool_name, COUNT(*) AS count
        FROM tool_calls
        WHERE run_id = ?
        GROUP BY tool_name
        ORDER BY count DESC, tool_name ASC
        LIMIT 10
        """,
        (run_id,),
    ).fetchall()
    step_row = conn.execute(
        """
        SELECT COUNT(*) AS total_steps, MIN(step_index) AS min_step, MAX(step_index) AS max_step
        FROM run_steps
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    time_row = conn.execute(
        """
        SELECT MIN(created_at) AS first_event, MAX(updated_at) AS last_event
        FROM tool_calls
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    side_effect_row = conn.execute(
        """
        SELECT COUNT(*) AS side_effect_succeeded
        FROM tool_calls
        WHERE run_id = ? AND status = 'succeeded' AND side_effect_class != 'read_only'
        """,
        (run_id,),
    ).fetchone()
    runwide_summary = {
        "total_tool_calls": summary_row["total_tool_calls"] if summary_row else 0,
        "status_counts": {
            "succeeded": summary_row["succeeded"] if summary_row else 0,
            "failed": summary_row["failed"] if summary_row else 0,
            "running": summary_row["running"] if summary_row else 0,
            "prepared": summary_row["prepared"] if summary_row else 0,
            "abandoned_on_resume": summary_row["abandoned"] if summary_row else 0,
            "replayed": summary_row["replayed"] if summary_row else 0,
        },
        "top_tools": [
            {"tool_name": row["tool_name"], "count": row["count"]}
            for row in tool_counts
        ],
        "steps": {
            "total_steps": step_row["total_steps"] if step_row else 0,
            "min_step": step_row["min_step"] if step_row else None,
            "max_step": step_row["max_step"] if step_row else None,
        },
        "timeline": {
            "first_event": time_row["first_event"] if time_row else None,
            "last_event": time_row["last_event"] if time_row else None,
        },
        "side_effect_succeeded": (
            side_effect_row["side_effect_succeeded"] if side_effect_row else 0
        ),
    }

    def _effects_from_receipts(receipt_items: list[dict[str, Any]]) -> set[str]:
        effects: set[str] = set()
        for receipt in receipt_items:
            tool_name = str(receipt.get("tool_name", "") or "")
            response_ref = str(receipt.get("response_ref", "") or "")
            tool_name_upper = tool_name.upper()
            haystack = f"{tool_name} {response_ref}".lower()
            if "GMAIL_SEND_EMAIL" in tool_name_upper:
                effects.add("email")
            elif "COMPOSIO_MULTI_EXECUTE_TOOL" in tool_name_upper:
                if "gmail_send_email" in haystack or "recipient_email" in haystack:
                    effects.add("email")
            elif "send_email" in haystack and "gmail" in haystack:
                effects.add("email")
            if "UPLOAD_TO_COMPOSIO" in tool_name_upper:
                effects.add("upload")
            elif "upload_to_composio" in haystack or (
                "upload" in haystack and "composio" in haystack
            ):
                effects.add("upload")
        return effects

    confirmed_effects = _effects_from_receipts(evidence_receipts)
    effect_labels = {
        "email": "Email sent",
        "upload": "Upload to Composio/S3",
    }
    runwide_line = None
    if runwide_summary["total_tool_calls"]:
        runwide_line = (
            "Run-wide: "
            f"{runwide_summary['total_tool_calls']} tools | "
            f"{runwide_summary['status_counts']['succeeded']} succeeded | "
            f"{runwide_summary['status_counts']['failed']} failed | "
            f"{runwide_summary['status_counts']['abandoned_on_resume']} abandoned | "
            f"{runwide_summary['status_counts']['replayed']} replayed | "
            f"{runwide_summary['steps']['total_steps']} steps"
        )

    print("\n" + "=" * 80)
    print("=== JOB COMPLETE ===")
    print(f"Run ID: {run_id}")
    print(f"Status: {status}")
    if main_trace_id:
        print(f"Main Trace ID: {main_trace_id}")
    if local_trace_ids:
        print("Related Trace IDs (local-toolkit):")
        for trace_id in local_trace_ids:
            print(f"- {trace_id}")
    if artifacts:
        print("Artifacts:")
        for name in artifacts:
            print(f"- {os.path.join(workspace_dir, name)}")
    if receipts:
        print("Last side-effect receipts:")
        for receipt in receipts:
            print(
                f"- {receipt['tool_name']} | {receipt['status']} | {receipt['idempotency_key']}"
            )
    if replayed:
        print("Replayed tools:")
        for row in replayed:
            print(f"- {row['tool_name']} | {row['replay_status']}")
    if abandoned:
        print("Abandoned tools:")
        for row in abandoned:
            print(f"- {row['tool_name']} | {row['outcome']}")
    summary = summarize_response(response_text)
    if summary:
        print("Summary:")
        print(summary)
    if confirmed_effects:
        print("Evidence summary (receipts only):")
        for effect in sorted(confirmed_effects):
            print(f"- {effect_labels.get(effect, effect)}")
    elif receipts:
        print("Evidence summary (receipts only):")
        print("- none")
    if runwide_summary["total_tool_calls"]:
        if runwide_line:
            print(runwide_line)
        print("Run-wide summary:")
        print(
            "Tool calls: "
            f"{runwide_summary['total_tool_calls']} total | "
            f"{runwide_summary['status_counts']['succeeded']} succeeded | "
            f"{runwide_summary['status_counts']['failed']} failed | "
            f"{runwide_summary['status_counts']['abandoned_on_resume']} abandoned | "
            f"{runwide_summary['status_counts']['replayed']} replayed"
        )
        print(
            "Steps: "
            f"{runwide_summary['steps']['total_steps']} total "
            f"(min {runwide_summary['steps']['min_step']}, "
            f"max {runwide_summary['steps']['max_step']})"
        )
        print(
            "Timeline: "
            f"{runwide_summary['timeline']['first_event']} → "
            f"{runwide_summary['timeline']['last_event']}"
        )
        if runwide_summary["top_tools"]:
            print("Top tools:")
            for row in runwide_summary["top_tools"]:
                print(f"- {row['tool_name']} | {row['count']}")
    print("=" * 80)

    summary_path = None
    if workspace_dir:
        summary_path = os.path.join(workspace_dir, f"job_completion_{run_id}.md")
        try:
            with open(summary_path, "w", encoding="utf-8") as f:
                f.write("# Job Completion Summary\n\n")
                f.write(f"Run ID: {run_id}\n\n")
                f.write(f"Status: {status}\n\n")
                if main_trace_id:
                    f.write(f"Main Trace ID: {main_trace_id}\n\n")
                if local_trace_ids:
                    f.write("Related Trace IDs (local-toolkit):\n")
                    for trace_id in local_trace_ids:
                        f.write(f"- {trace_id}\n")
                    f.write("\n")
                if artifacts:
                    f.write("Artifacts:\n")
                    for name in artifacts:
                        f.write(f"- {os.path.join(workspace_dir, name)}\n")
                    f.write("\n")
                if receipts:
                    f.write("Last side-effect receipts:\n")
                    for receipt in receipts:
                        f.write(
                            f"- {receipt['tool_name']} | {receipt['status']} | {receipt['idempotency_key']}\n"
                        )
                    f.write("\n")
                if replayed:
                    f.write("Replayed tools:\n")
                    for row in replayed:
                        f.write(f"- {row['tool_name']} | {row['replay_status']}\n")
                    f.write("\n")
                if abandoned:
                    f.write("Abandoned tools:\n")
                    for row in abandoned:
                        f.write(f"- {row['tool_name']} | {row['outcome']}\n")
                    f.write("\n")
                if summary:
                    f.write("Summary:\n")
                    f.write(summary + "\n")
                if confirmed_effects or receipts:
                    f.write("Evidence summary (receipts only):\n")
                    if confirmed_effects:
                        for effect in sorted(confirmed_effects):
                            f.write(f"- {effect_labels.get(effect, effect)}\n")
                    else:
                        f.write("- none\n")
                if runwide_summary["total_tool_calls"]:
                    f.write("\nRun-wide summary:\n")
                    if runwide_line:
                        f.write(runwide_line + "\n")
                    f.write(
                        "Tool calls: "
                        f"{runwide_summary['total_tool_calls']} total | "
                        f"{runwide_summary['status_counts']['succeeded']} succeeded | "
                        f"{runwide_summary['status_counts']['failed']} failed | "
                        f"{runwide_summary['status_counts']['abandoned_on_resume']} abandoned | "
                        f"{runwide_summary['status_counts']['replayed']} replayed\n"
                    )
                    f.write(
                        "Steps: "
                        f"{runwide_summary['steps']['total_steps']} total "
                        f"(min {runwide_summary['steps']['min_step']}, "
                        f"max {runwide_summary['steps']['max_step']})\n"
                    )
                    f.write(
                        "Timeline: "
                        f"{runwide_summary['timeline']['first_event']} → "
                        f"{runwide_summary['timeline']['last_event']}\n"
                    )
                    if runwide_summary["top_tools"]:
                        f.write("Top tools:\n")
                        for row in runwide_summary["top_tools"]:
                            f.write(f"- {row['tool_name']} | {row['count']}\n")
        except Exception as exc:
            print(f"⚠️ Failed to save job completion summary: {exc}")
    if summary_path and update_restart_file_cb:
        update_restart_file_cb(
            run_id,
            workspace_dir,
            None,
            None,
            summary_path,
            runwide_line,
        )
