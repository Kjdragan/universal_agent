"""
Universal Agent Core - Refactored agent logic for both CLI and API usage.

This module provides the UniversalAgent class which encapsulates:
- Composio/Claude SDK initialization
- Conversation handling with streaming events
- Observer pattern for artifact saving
"""

import asyncio
import os
import time
import json
import re
from datetime import datetime
from typing import AsyncGenerator, Any, Callable, Optional
from dataclasses import dataclass, field
from enum import Enum

from dotenv import load_dotenv

load_dotenv()

import logfire
from claude_agent_sdk.client import ClaudeSDKClient
from claude_agent_sdk.types import (
    ClaudeAgentOptions,
    AgentDefinition,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
    UserMessage,
)
from composio import Composio


# =============================================================================
# EVENT TYPES - Used for streaming events back to CLI/UI
# =============================================================================


class EventType(str, Enum):
    """Types of events emitted by the agent."""

    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    THINKING = "thinking"
    STATUS = "status"
    AUTH_REQUIRED = "auth_required"
    ERROR = "error"
    SESSION_INFO = "session_info"
    ITERATION_END = "iteration_end"
    WORK_PRODUCT = "work_product"  # HTML reports, files saved to work_products/


@dataclass
class AgentEvent:
    """An event emitted by the agent during execution."""

    type: EventType
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


# =============================================================================
# LOGFIRE SETUP
# =============================================================================


LOGFIRE_TOKEN = (
    os.getenv("LOGFIRE_TOKEN")
    or os.getenv("LOGFIRE_WRITE_TOKEN")
    or os.getenv("LOGFIRE_API_KEY")
)


def configure_logfire():
    """Configure Logfire for tracing if token is available."""
    if not LOGFIRE_TOKEN:
        return False

    def scrubbing_callback(m: logfire.ScrubMatch):
        if not m.path:
            return None
        last_key = m.path[-1]
        if isinstance(last_key, str):
            if (
                last_key
                in (
                    "content_preview",
                    "input_preview",
                    "result_preview",
                    "text_preview",
                    "query",
                    "input",
                )
                or "preview" in last_key
            ):
                return m.value
        return None

    logfire.configure(
        service_name="universal-agent",
        environment="development",
        console=False,
        token=LOGFIRE_TOKEN,
        send_to_logfire="if-token-present",
        scrubbing=logfire.ScrubbingOptions(callback=scrubbing_callback),
    )

    try:
        logfire.instrument_mcp()
    except Exception:
        pass

    try:
        logfire.instrument_httpx(capture_headers=True)
    except Exception:
        pass

    return True


# =============================================================================
# HELPER FUNCTIONS (Observer pattern, date parsing, etc.)
# =============================================================================


def parse_relative_date(relative_str: str) -> str:
    """Convert '2 hours ago' to YYYY-MM-DD format."""
    from datetime import timedelta

    now = datetime.now()
    if not relative_str:
        return now.strftime("%Y-%m-%d")

    match = re.match(
        r"(\d+)\s*(hour|minute|day|week|month)s?\s*ago", relative_str.lower()
    )
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        deltas = {
            "minute": timedelta(minutes=value),
            "hour": timedelta(hours=value),
            "day": timedelta(days=value),
            "week": timedelta(weeks=value),
            "month": timedelta(days=value * 30),
        }
        return (now - deltas.get(unit, timedelta())).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def _update_domain_blacklist(workspace_dir: str, domain: str, error_code: str) -> None:
    """Track domain failures for blacklist learning."""
    try:
        base_workspace = os.path.dirname(workspace_dir) if workspace_dir else "."
        blacklist_path = os.path.join(base_workspace, "webReader_blacklist.json")

        blacklist = {"domains": {}, "threshold": 3}
        if os.path.exists(blacklist_path):
            with open(blacklist_path, "r") as f:
                blacklist = json.load(f)

        if error_code == "1214":
            if domain not in blacklist["domains"]:
                blacklist["domains"][domain] = {"failures": 0, "last_failure": ""}

            blacklist["domains"][domain]["failures"] += 1
            blacklist["domains"][domain]["last_failure"] = datetime.now().isoformat()

            if blacklist["domains"][domain]["failures"] >= blacklist["threshold"]:
                logfire.warning(
                    "domain_blacklisted",
                    domain=domain,
                    failures=blacklist["domains"][domain]["failures"],
                )

            with open(blacklist_path, "w") as f:
                json.dump(blacklist, f, indent=2)

    except Exception as e:
        logfire.debug("blacklist_update_error", error=str(e))


# =============================================================================
# OBSERVER FUNCTIONS
# =============================================================================


async def observe_and_save_search_results(
    tool_name: str, content, workspace_dir: str
) -> None:
    """Observer: Parse SERP tool results and save cleaned artifacts."""
    is_serp_tool = any(
        kw in tool_name.upper()
        for kw in ["SEARCH_NEWS", "SEARCH_WEB", "COMPOSIO_SEARCH", "MULTI_EXECUTE"]
    )

    if not is_serp_tool:
        return

    try:
        raw_json = None
        if isinstance(content, list):
            for item in content:
                if hasattr(item, "text"):
                    raw_json = item.text
                    break
                elif isinstance(item, dict) and item.get("type") == "text":
                    raw_json = item.get("text", "")
                    break
        elif isinstance(content, str):
            raw_json = content

        if not raw_json:
            return

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return

        if not isinstance(data, dict):
            return

        payloads = []
        root = data
        if isinstance(root, dict) and "data" in root:
            root = root["data"]

        if (
            isinstance(root, dict)
            and "results" in root
            and isinstance(root["results"], list)
        ):
            for item in root["results"]:
                if isinstance(item, dict) and "response" in item:
                    inner_resp = item["response"]
                    if isinstance(inner_resp, str):
                        try:
                            inner_resp = json.loads(inner_resp)
                        except json.JSONDecodeError:
                            continue
                    if isinstance(inner_resp, dict):
                        inner_data = inner_resp.get("data") or inner_resp.get(
                            "data_preview"
                        )
                        inner_slug = item.get("tool_slug", tool_name)
                        if inner_data:
                            payloads.append((inner_slug, inner_data))
        else:
            payloads.append((tool_name, root))

        saved_count = 0
        for slug, payload in payloads:
            if not isinstance(payload, dict):
                continue

            search_data = payload
            if "results" in payload and isinstance(payload["results"], dict):
                search_data = payload["results"]

            def safe_get_list(data, key):
                val = data.get(key, [])
                if isinstance(val, dict):
                    return list(val.values())
                if isinstance(val, list):
                    return val
                return []

            cleaned = None

            if "news_results" in search_data:
                raw_list = safe_get_list(search_data, "news_results")
                cleaned = {
                    "type": "news",
                    "timestamp": datetime.now().isoformat(),
                    "tool": slug,
                    "articles": [
                        {
                            "position": a.get("position") or (idx + 1),
                            "title": a.get("title"),
                            "url": a.get("link"),
                            "source": a.get("source", {}).get("name")
                            if isinstance(a.get("source"), dict)
                            else a.get("source"),
                            "date": parse_relative_date(a.get("date", "")),
                            "snippet": a.get("snippet"),
                        }
                        for idx, a in enumerate(raw_list)
                        if isinstance(a, dict)
                    ],
                }
            elif "organic_results" in search_data:
                raw_list = safe_get_list(search_data, "organic_results")
                cleaned = {
                    "type": "web",
                    "timestamp": datetime.now().isoformat(),
                    "tool": slug,
                    "results": [
                        {
                            "position": r.get("position") or (idx + 1),
                            "title": r.get("title"),
                            "url": r.get("link"),
                            "snippet": r.get("snippet"),
                        }
                        for idx, r in enumerate(raw_list)
                    ],
                }

            if cleaned and workspace_dir:
                filename = "unknown"
                try:
                    search_dir = os.path.join(workspace_dir, "search_results")
                    os.makedirs(search_dir, exist_ok=True)

                    timestamp_str = datetime.now().strftime("%H%M%S")
                    suffix = f"_{saved_count}" if len(payloads) > 1 else ""
                    filename = os.path.join(
                        search_dir, f"{slug}{suffix}_{timestamp_str}.json"
                    )

                    with open(filename, "w") as f:
                        json.dump(cleaned, f, indent=2)

                    if os.path.exists(filename):
                        logfire.info(
                            "observer_artifact_saved",
                            path=filename,
                            type=cleaned.get("type"),
                        )
                        saved_count += 1
                except Exception as file_error:
                    logfire.error("observer_file_io_error", error=str(file_error))

    except Exception as e:
        logfire.warning("observer_error", tool=tool_name, error=str(e))


async def observe_and_save_workbench_activity(
    tool_name: str, tool_input: dict, tool_result: str, workspace_dir: str
) -> None:
    """Observer: Capture COMPOSIO_REMOTE_WORKBENCH activity."""
    if "REMOTE_WORKBENCH" not in tool_name.upper():
        return

    try:
        workbench_dir = os.path.join(workspace_dir, "workbench_activity")
        os.makedirs(workbench_dir, exist_ok=True)

        timestamp_str = datetime.now().strftime("%H%M%S")
        filename = os.path.join(workbench_dir, f"workbench_{timestamp_str}.json")

        result_data = {}
        try:
            if isinstance(tool_result, str):
                import ast

                parsed_list = ast.literal_eval(tool_result)
                for item in parsed_list:
                    if isinstance(item, dict) and item.get("type") == "text":
                        result_json = json.loads(item.get("text", "{}"))
                        result_data = result_json.get("data", {})
                        break
        except (json.JSONDecodeError, ValueError, SyntaxError):
            result_data = {
                "raw": tool_result[:500] if isinstance(tool_result, str) else ""
            }

        activity_log = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "input": {
                "code": tool_input.get("code_to_execute", "")[:1000],
                "session_id": tool_input.get("session_id"),
            },
            "output": {
                "stdout": result_data.get("stdout", ""),
                "stderr": result_data.get("stderr", ""),
                "successful": result_data.get("successful"),
            },
        }

        with open(filename, "w") as f:
            json.dump(activity_log, f, indent=2)

        logfire.info("workbench_activity_saved", path=filename)

    except Exception as e:
        logfire.warning("workbench_observer_error", tool=tool_name, error=str(e))


async def observe_and_enrich_corpus(
    tool_name: str, tool_input: dict, tool_content, workspace_dir: str
) -> None:
    """Observer: Capture webReader article extraction results."""
    if "webreader" not in tool_name.lower():
        return

    try:
        raw_json = None
        if isinstance(tool_content, list):
            for item in tool_content:
                if hasattr(item, "text"):
                    raw_json = item.text
                    break
                elif isinstance(item, dict) and item.get("type") == "text":
                    raw_json = item.get("text", "")
                    break
        elif isinstance(tool_content, str):
            raw_json = tool_content

        if not raw_json:
            return

        if "MCP error" in raw_json:
            url = tool_input.get("url", "") if tool_input else ""
            if '"code":"1214"' in raw_json:
                try:
                    from urllib.parse import urlparse

                    domain = urlparse(url).netloc
                    if domain:
                        _update_domain_blacklist(workspace_dir, domain, "1214")
                except Exception:
                    pass
            return

        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return

        reader_result = data.get("reader_result", data)
        content_data = {
            "title": reader_result.get("title", ""),
            "description": reader_result.get("description", ""),
            "content": reader_result.get("content", ""),
            "url": reader_result.get("url", tool_input.get("url", "")),
        }

        articles_dir = os.path.join(workspace_dir, "extracted_articles")
        os.makedirs(articles_dir, exist_ok=True)

        timestamp_str = datetime.now().strftime("%H%M%S")
        url = tool_input.get("url", "unknown")
        safe_name = (
            url.replace("https://", "").replace("http://", "").replace("/", "_")[:50]
        )
        filename = os.path.join(articles_dir, f"{safe_name}_{timestamp_str}.json")

        article_record = {
            "timestamp": datetime.now().isoformat(),
            "source_url": url,
            "title": content_data.get("title", ""),
            "description": content_data.get("description", ""),
            "content": content_data.get("content", "")[:10000],
            "extraction_success": bool(content_data.get("content")),
        }

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(article_record, f, indent=2, ensure_ascii=False)

        logfire.info("article_extracted", url=url, path=filename)

    except Exception as e:
        logfire.warning("webreader_observer_error", tool=tool_name, error=str(e))


# =============================================================================
# UNIVERSAL AGENT CLASS
# =============================================================================


class UniversalAgent:
    """
    Core agent class that can be driven by CLI or API.

    Emits AgentEvent objects via async generator for UI/CLI to consume.
    """

    def __init__(self, workspace_dir: Optional[str] = None, user_id: str = "user_123"):
        self.user_id = user_id
        self.workspace_dir = workspace_dir or self._create_workspace()
        self.trace: dict = {}
        self.start_ts: float = 0
        self.composio: Optional[Composio] = None
        self.session = None
        self.options: Optional[ClaudeAgentOptions] = None
        self.client: Optional[ClaudeSDKClient] = None
        self._initialized = False

    def _create_workspace(self) -> str:
        """Create a new session workspace directory."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace_dir = os.path.join("AGENT_RUN_WORKSPACES", f"session_{timestamp}")
        os.makedirs(workspace_dir, exist_ok=True)
        return workspace_dir

    async def initialize(self) -> None:
        """Initialize Composio, Claude SDK, and tracing."""
        if self._initialized:
            return

        # Initialize Composio
        downloads_dir = os.path.join(self.workspace_dir, "downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        self.composio = Composio(
            api_key=os.environ["COMPOSIO_API_KEY"], file_download_dir=downloads_dir
        )
        self.session = self.composio.create(user_id=self.user_id)

        # Build system prompt
        import sys

        abs_workspace = os.path.abspath(self.workspace_dir)
        system_prompt = self._build_system_prompt(abs_workspace)

        self.options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={
                "composio": {
                    "type": "http",
                    "url": self.session.mcp.url,
                    "headers": {"x-api-key": os.environ["COMPOSIO_API_KEY"]},
                },
                "local_toolkit": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": ["src/mcp_server.py"],
                },
                "web_reader": {
                    "type": "http",
                    "url": "https://api.z.ai/api/mcp/web_reader/mcp",
                    "headers": {
                        "Authorization": f"Bearer {os.environ.get('ZAI_API_KEY', '')}"
                    },
                },
            },
            allowed_tools=["Task"],
            agents={
                "report-creation-expert": AgentDefinition(
                    description=(
                        "MANDATORY SUB-AGENT for ALL research and report tasks. "
                        "ALWAYS delegate when user requests: report, analysis, research, summary, comprehensive, detailed. "
                        "This sub-agent extracts content from URLs, creates professional HTML reports, and saves to work_products/. "
                        "DO NOT handle reports yourself - delegate here."
                    ),
                    prompt=self._build_subagent_prompt(abs_workspace),
                    tools=[
                        "mcp__web_reader__webReader",
                        "mcp__local_toolkit__save_corpus",
                        "mcp__local_toolkit__write_local_file",
                        "mcp__local_toolkit__workbench_download",
                        "mcp__local_toolkit__workbench_upload",
                    ],
                    model="inherit",
                ),
            },
            permission_mode="bypassPermissions",
        )

        # Initialize trace
        self.trace = {
            "session_info": {
                "url": self.session.mcp.url,
                "user_id": self.user_id,
                "timestamp": datetime.now().isoformat(),
            },
            "query": None,
            "start_time": None,
            "end_time": None,
            "total_duration_seconds": None,
            "tool_calls": [],
            "tool_results": [],
            "iterations": [],
            "logfire_enabled": bool(LOGFIRE_TOKEN),
        }

        self._initialized = True

    def _build_system_prompt(self, workspace_path: str) -> str:
        """Build the main system prompt."""
        return (
            f"Result Date: {datetime.now().strftime('%A, %B %d, %Y')}\n"
            "TEMPORAL CONSISTENCY WARNING: You are operating in a timeline where it is December 2025. "
            "If 'real-world' search tools return results dated 2024, explicitly note the date discrepancy.\n\n"
            "You are a helpful assistant with access to external tools and specialized sub-agents.\n\n"
            "## CRITICAL DELEGATION RULES\n\n"
            "You MUST delegate to `report-creation-expert` sub-agent when the user's request involves ANY of:\n"
            "- Creating a report, summary, or analysis\n"
            "- Research that requires synthesizing multiple sources\n"
            "- Keywords: 'report', 'comprehensive', 'detailed', 'analysis', 'research', 'summarize'\n"
            "- Sending findings via email (the sub-agent creates proper HTML attachments)\n\n"
            "DO NOT attempt to create reports yourself using COMPOSIO tools directly. "
            "The sub-agent has specialized tools (webReader, save_corpus, write_local_file) that produce "
            "higher quality outputs and save work products for the user.\n\n"
            "## EMAIL REQUIREMENTS\n\n"
            "When sending reports via email:\n"
            "1. ALWAYS delegate report creation to `report-creation-expert` first\n"
            "2. Reports must be saved as HTML files in work_products/\n"
            "3. Use GMAIL_SEND_EMAIL_WITH_ATTACHMENT to ATTACH the HTML file\n"
            "4. Do NOT inject full HTML into the email body - attach it instead\n"
            "5. Email body should be a brief summary (1-2 paragraphs) + 'See attached report'\n\n"
            "## EXECUTION GUIDELINES\n"
            "- When the user requests an action, proceed immediately without asking for confirmation.\n"
            "- Complete the full task end-to-end in a single workflow.\n"
            "- For simple factual questions, you can respond directly.\n"
            "- For any research/report task, delegate to sub-agent.\n\n"
            f"Context:\nCURRENT_SESSION_WORKSPACE: {workspace_path}\n"
        )

    def _build_subagent_prompt(self, workspace_path: str) -> str:
        """Build the report-creation-expert sub-agent prompt."""
        return (
            f"Result Date: {datetime.now().strftime('%A, %B %d, %Y')}\n"
            f"CURRENT_SESSION_WORKSPACE: {workspace_path}\n\n"
            "You are a **Report Creation Expert** sub-agent. Your job is to create high-quality, "
            "professional HTML reports and ensure they are properly saved and delivered.\n\n"
            "## MANDATORY WORKFLOW\n\n"
            "### Step 1: Research & Extraction\n"
            "- Use `webReader` to extract full content from source URLs (max 10 articles)\n"
            "- Focus on extracting key facts, quotes, and data\n"
            "- Save extracted content using `save_corpus` tool\n\n"
            "### Step 2: Report Creation\n"
            "- Synthesize a professional HTML report with:\n"
            "  - Executive summary\n"
            "  - Key findings/developments\n"
            "  - Analysis and implications\n"
            "  - Source citations\n"
            "- Use proper HTML5 structure with CSS styling\n"
            "- Make it visually appealing (colors, sections, formatting)\n\n"
            "### Step 3: Save Work Product\n"
            f"- ALWAYS save the HTML report to: {workspace_path}/work_products/\n"
            "- Use `write_local_file` with descriptive filename (e.g., `russia_ukraine_report_2025-12-23.html`)\n"
            "- This is CRITICAL - the report MUST be saved as a file\n\n"
            "### Step 4: Email Delivery (if requested)\n"
            "When asked to email the report:\n"
            "- Return control to the main agent with the HTML report content\n"
            "- Instruct that the email should use `is_html: true`\n"
            "- The report saved in work_products/ serves as the authoritative copy\n\n"
            "## OUTPUT REQUIREMENTS\n"
            "- Reports must be in HTML format, never plain markdown\n"
            "- Include inline CSS for styling (dark theme preferred)\n"
            "- Reports should be self-contained and readable standalone\n"
        )

    async def run_query(self, query: str) -> AsyncGenerator[AgentEvent, None]:
        """
        Run a query and yield events as they happen.

        This is the main entry point for both CLI and API usage.
        """
        if not self._initialized:
            await self.initialize()

        self.trace["query"] = query
        self.trace["start_time"] = datetime.now().isoformat()
        self.start_ts = time.time()

        # Emit session info
        yield AgentEvent(
            type=EventType.SESSION_INFO,
            data={
                "session_url": self.session.mcp.url,
                "workspace": self.workspace_dir,
                "user_id": self.user_id,
            },
        )

        async with ClaudeSDKClient(self.options) as client:
            self.client = client

            # Run the conversation
            async for event in self._run_conversation(query, iteration=1):
                yield event

        # Save trace
        self._save_trace()

    async def _run_conversation(
        self, query: str, iteration: int
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run a single conversation turn."""
        yield AgentEvent(
            type=EventType.STATUS, data={"status": "processing", "iteration": iteration}
        )

        await self.client.query(query)

        tool_calls_this_iter = []
        auth_link = None

        async for msg in self.client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tool_record = {
                            "iteration": iteration,
                            "name": block.name,
                            "id": block.id,
                            "time_offset_seconds": round(
                                time.time() - self.start_ts, 3
                            ),
                            "input": block.input if hasattr(block, "input") else None,
                        }
                        self.trace["tool_calls"].append(tool_record)
                        tool_calls_this_iter.append(tool_record)

                        yield AgentEvent(
                            type=EventType.TOOL_CALL,
                            data={
                                "name": block.name,
                                "id": block.id,
                                "input": block.input,
                                "time_offset": tool_record["time_offset_seconds"],
                            },
                        )

                    elif isinstance(block, TextBlock):
                        # Check for auth link
                        if "connect.composio.dev/link" in block.text:
                            links = re.findall(
                                r"https://connect\.composio\.dev/link/[^\s\)]+",
                                block.text,
                            )
                            if links:
                                auth_link = links[0]
                                yield AgentEvent(
                                    type=EventType.AUTH_REQUIRED,
                                    data={"auth_link": auth_link},
                                )

                        yield AgentEvent(type=EventType.TEXT, data={"text": block.text})

                    elif isinstance(block, ThinkingBlock):
                        yield AgentEvent(
                            type=EventType.THINKING,
                            data={"thinking": block.thinking[:500]},
                        )

            elif isinstance(msg, (UserMessage, ToolResultBlock)):
                blocks = msg.content if isinstance(msg, UserMessage) else [msg]

                for block in blocks:
                    is_result = isinstance(block, ToolResultBlock) or hasattr(
                        block, "tool_use_id"
                    )

                    if is_result:
                        tool_use_id = getattr(block, "tool_use_id", None)
                        is_error = getattr(block, "is_error", False)
                        block_content = getattr(block, "content", "")
                        content_str = str(block_content)

                        result_record = {
                            "tool_use_id": tool_use_id,
                            "time_offset_seconds": round(
                                time.time() - self.start_ts, 3
                            ),
                            "is_error": is_error,
                            "content_size_bytes": len(content_str),
                        }
                        self.trace["tool_results"].append(result_record)

                        yield AgentEvent(
                            type=EventType.TOOL_RESULT,
                            data={
                                "tool_use_id": tool_use_id,
                                "is_error": is_error,
                                "content_preview": content_str[:500],
                                "content_size": len(content_str),
                            },
                        )

                        # Fire observers
                        tool_name = None
                        tool_input = None
                        for tc in tool_calls_this_iter:
                            if tc.get("id") == tool_use_id:
                                tool_name = tc.get("name")
                                tool_input = tc.get("input", {})
                                break

                        if tool_name and self.workspace_dir:
                            asyncio.create_task(
                                observe_and_save_search_results(
                                    tool_name, block_content, self.workspace_dir
                                )
                            )
                            asyncio.create_task(
                                observe_and_save_workbench_activity(
                                    tool_name,
                                    tool_input or {},
                                    content_str,
                                    self.workspace_dir,
                                )
                            )
                            asyncio.create_task(
                                observe_and_enrich_corpus(
                                    tool_name,
                                    tool_input or {},
                                    block_content,
                                    self.workspace_dir,
                                )
                            )

                            # Check for work_product events (write_local_file to work_products/)
                            if "write_local_file" in tool_name.lower() and tool_input:
                                file_path = tool_input.get("file_path", "")
                                if "work_products" in file_path and file_path.endswith(
                                    ".html"
                                ):
                                    # Read the file content and emit work_product event
                                    try:
                                        if os.path.exists(file_path):
                                            with open(
                                                file_path, "r", encoding="utf-8"
                                            ) as f:
                                                html_content = f.read()
                                            yield AgentEvent(
                                                type=EventType.WORK_PRODUCT,
                                                data={
                                                    "content_type": "text/html",
                                                    "content": html_content,
                                                    "filename": os.path.basename(
                                                        file_path
                                                    ),
                                                    "path": file_path,
                                                },
                                            )
                                    except Exception as e:
                                        logfire.warning(
                                            "work_product_read_error", error=str(e)
                                        )

        yield AgentEvent(
            type=EventType.ITERATION_END,
            data={
                "iteration": iteration,
                "tool_calls": len(tool_calls_this_iter),
                "duration_seconds": round(time.time() - self.start_ts, 3),
            },
        )

    def _save_trace(self) -> None:
        """Save the trace to workspace."""
        self.trace["end_time"] = datetime.now().isoformat()
        self.trace["total_duration_seconds"] = round(time.time() - self.start_ts, 3)

        trace_path = os.path.join(self.workspace_dir, "trace.json")
        with open(trace_path, "w") as f:
            json.dump(self.trace, f, indent=2, default=str)

    def get_session_info(self) -> dict:
        """Return session info for display."""
        return {
            "workspace": self.workspace_dir,
            "user_id": self.user_id,
            "session_url": self.session.mcp.url if self.session else None,
            "logfire_enabled": bool(LOGFIRE_TOKEN),
        }
