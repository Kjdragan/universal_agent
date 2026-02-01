"""
AgentSetup - Unified initialization for CLI, API, and URW harness.

This module provides a single source of truth for agent configuration,
ensuring consistent behavior across all entry points.
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

from composio import Composio
from claude_agent_sdk.types import ClaudeAgentOptions, HookMatcher

from universal_agent.prompt_assets import (
    discover_skills,
    generate_skills_xml,
    get_tool_knowledge_block,
)
from claude_agent_sdk import create_sdk_mcp_server
from universal_agent.tools.research_bridge import (
    run_research_pipeline_wrapper,
    crawl_parallel_wrapper,
    run_report_generation_wrapper,
    run_research_phase_wrapper,
    generate_outline_wrapper,
    draft_report_parallel_wrapper,
    cleanup_report_wrapper,
    compile_report_wrapper,
)
from universal_agent.tools.memory import ua_memory_get
from universal_agent.execution_context import bind_workspace_env
from universal_agent.feature_flags import memory_index_enabled


# Get project directories
def _get_src_dir() -> str:
    """Get the repository root directory."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(current_dir))


from universal_agent.constants import DISALLOWED_TOOLS


class AgentSetup:
    """
    Unified agent initialization for CLI, API, and URW harness.
    
    Separates session initialization from workspace binding to support:
    - CLI: Single session, single workspace
    - API: Single session, single workspace per WebSocket connection
    - URW: Single session, multiple workspaces per phase
    """

    def __init__(
        self,
        workspace_dir: str,
        user_id: Optional[str] = None,
        enable_skills: bool = True,
        enable_memory: bool = False,
        verbose: bool = True,
    ):
        self.workspace_dir = workspace_dir
        from universal_agent.identity.resolver import resolve_user_id
        self.workspace_dir = workspace_dir
        self.user_id = resolve_user_id(user_id)
        self.enable_skills = enable_skills
        disable_memory = os.getenv("UA_DISABLE_LOCAL_MEMORY", "").lower() in {"1", "true", "yes"}
        self.enable_memory = enable_memory and memory_index_enabled() and not disable_memory
        self.verbose = verbose
        
        self.run_id = str(uuid.uuid4())
        self.src_dir = _get_src_dir()
        
        # Initialized by initialize()
        self._composio: Optional[Composio] = None
        self._session: Optional[Any] = None
        self._options: Optional[ClaudeAgentOptions] = None
        self._initialized = False
        
        # Cached discovery results
        self._discovered_apps: list[str] = []
        self._discovered_skills: list[dict] = []
        self._skills_xml: str = ""
        self._memory_context: str = ""
        self._soul_context: str = ""
        
        # Hooks (set by main.py or can use defaults)
        self._hooks: dict = {}

    @property
    def session(self):
        """Get the Composio session."""
        if not self._initialized:
            raise RuntimeError("AgentSetup not initialized. Call initialize() first.")
        return self._session

    @property
    def options(self) -> ClaudeAgentOptions:
        """Get the configured ClaudeAgentOptions."""
        if not self._initialized:
            raise RuntimeError("AgentSetup not initialized. Call initialize() first.")
        return self._options  # type: ignore

    @property
    def composio(self) -> Composio:
        """Get the Composio client."""
        if not self._initialized:
            raise RuntimeError("AgentSetup not initialized. Call initialize() first.")
        return self._composio  # type: ignore

    def set_hooks(self, hooks: dict) -> None:
        """Set custom hooks to use in ClaudeAgentOptions."""
        self._hooks = hooks

    async def initialize(self) -> None:
        """
        Initialize Composio session and build ClaudeAgentOptions.
        
        This performs all the heavy lifting:
        - Creates Composio session
        - Discovers connected apps
        - Loads skills
        - Builds system prompt
        - Configures MCP servers
        """
        if self._initialized:
            return

        # Ensure workspace directories exist
        self._setup_workspace_dirs()

        # Set workspace in environment for MCP server subprocess
        bind_workspace_env(self.workspace_dir)

        # Initialize Composio
        downloads_dir = os.path.join(self.workspace_dir, "downloads")
        self._composio = Composio(
            api_key=os.environ.get("COMPOSIO_API_KEY", ""),
            file_download_dir=downloads_dir,
        )

        # Create session and discover apps in parallel
        self._log("⏳ Starting Composio Session initialization...")
        session_future = asyncio.to_thread(
            self._composio.create,
            user_id=self.user_id,
            toolkits={"disable": ["firecrawl", "exa"]},
        )

        self._log("⏳ Discovering connected apps...")
        discovery_future = self._discover_apps_async()

        # Await session first (critical path)
        self._session = await session_future
        self._log("✅ Composio Session Created")

        # Await discovery
        self._discovered_apps = await discovery_future
        
        # Ensure core apps are always available
        core_apps = ["gmail", "composio_search", "browserbase", "github", "codeinterpreter"]
        if not self._discovered_apps:
            self._discovered_apps = []
            
        for app in core_apps:
            if app not in self._discovered_apps:
                self._discovered_apps.append(app)
                
        self._log(f"✅ Active Apps (Discovered + Core): {self._discovered_apps}")

        # Discover skills
        if self.enable_skills:
            self._discovered_skills = discover_skills()
            skill_names = [s["name"] for s in self._discovered_skills]
            self._log(f"✅ Discovered Skills: {skill_names}")
            self._skills_xml = generate_skills_xml(self._discovered_skills)

        # Load memory context
        if self.enable_memory:
            self._memory_context = await self._load_memory_context()
        
        # Load soul/persona
        self._load_soul_context()
        
        # Build options
        self._options = self._build_options()
        self._initialized = True

    async def _discover_apps_async(self) -> list[str]:
        """Discover connected Composio apps."""
        try:
            from universal_agent.utils.composio_discovery import discover_connected_toolkits
            return await asyncio.to_thread(
                discover_connected_toolkits, self._composio, self.user_id
            )
        except Exception as e:
            self._log(f"⚠️ App discovery failed: {e}")
            return []

    async def _load_memory_context(self) -> str:
        """Load memory context for system prompt."""
        try:
            from Memory_System.manager import MemoryManager
            from universal_agent.agent_college.integration import setup_agent_college

            storage_path = os.getenv(
                "PERSIST_DIRECTORY", os.path.join(self.src_dir, "Memory_System_Data")
            )
            mem_mgr = MemoryManager(storage_dir=storage_path)
            setup_agent_college(mem_mgr)
            context = mem_mgr.get_system_prompt_addition()
            self._log(f"🧠 Injected Core Memory Context ({len(context)} chars)")
            return context
        except Exception as e:
            self._log(f"⚠️ Failed to load Memory Context: {e}")
            return ""

    def _load_soul_context(self):
        """Load the 'Soul' (Persona/Identity) from SOUL.md."""
        # Priority 1: Session Workspace (Task-specific override)
        workspace_soul = os.path.join(self.workspace_dir, "SOUL.md")
        # Priority 2: Centralized Prompt Assets (The Codebase Persona)
        assets_soul = os.path.join(self.src_dir, "src", "universal_agent", "prompt_assets", "SOUL.md")
        # Priority 3: Repo Root (Legacy/Fallback)
        root_soul = os.path.join(self.src_dir, "..", "..", "SOUL.md")
        
        soul_path = None
        if os.path.exists(workspace_soul):
            soul_path = workspace_soul
            self._log(f"👻 Loaded Soul override from workspace: {soul_path}")
        elif os.path.exists(assets_soul):
            soul_path = assets_soul
            self._log(f"👻 Loaded Standard Soul from assets: {soul_path}")
        elif os.path.exists(root_soul):
            # Resolve path relative to src_dir for robustness
            soul_path = root_soul
            self._log(f"👻 Loaded Legacy Soul from root: {soul_path}")
            
        if soul_path and os.path.exists(soul_path):
            try:
                with open(soul_path, "r", encoding="utf-8") as f:
                    self._soul_context = f.read().strip()
            except Exception as e:
                self._log(f"⚠️ Failed to read SOUL.md: {e}")
        else:
             self._log("👻 No SOUL.md found. Running in default Checkpoint mode.")

    def bind_workspace(self, new_workspace: str) -> None:
        """
        Update workspace path without recreating the session.
        
        Used by URW harness for phase transitions.
        """
        self.workspace_dir = new_workspace
        bind_workspace_env(new_workspace)
        self._setup_workspace_dirs()
        
        # Rebuild options with new workspace
        if self._initialized:
            self._options = self._build_options()

    def _setup_workspace_dirs(self) -> None:
        """Create standard workspace directory structure."""
        os.makedirs(self.workspace_dir, exist_ok=True)
        os.makedirs(os.path.join(self.workspace_dir, "downloads"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_dir, "work_products", "media"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_dir, "work_products", "media"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_dir, "search_results"), exist_ok=True)
        
        # Memory scaffolding
        if self.enable_memory:
            os.makedirs(os.path.join(self.workspace_dir, "memory"), exist_ok=True)
            memory_file = os.path.join(self.workspace_dir, "MEMORY.md")
            if not os.path.exists(memory_file):
                with open(memory_file, "w") as f:
                    f.write("# Agent Memory\n\nPersistent context for the agent.\n")

    def _build_options(self) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions with full configuration."""
        # Build system prompt
        system_prompt = self._build_system_prompt()
        
        # Build MCP servers config
        mcp_servers = self._build_mcp_servers()
        
        # Build disallowed tools list
        disallowed_tools = list(DISALLOWED_TOOLS)
        if not self.enable_memory:
            disallowed_tools.extend([
                "mcp__local_toolkit__core_memory_replace",
                "mcp__local_toolkit__core_memory_append",
                "mcp__local_toolkit__archival_memory_insert",
                "mcp__local_toolkit__archival_memory_search",
                "mcp__local_toolkit__get_core_memory_blocks",
            ])

        return ClaudeAgentOptions(
            model="claude-3-5-sonnet-20241022",
            add_dirs=[os.path.join(self.src_dir, ".claude")],
            setting_sources=["project"],  # Enable loading agents from .claude/agents/
            disallowed_tools=disallowed_tools,
            env={
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": os.getenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000"),
                "MAX_MCP_OUTPUT_TOKENS": os.getenv("MAX_MCP_OUTPUT_TOKENS", "64000"),
            },
            system_prompt=system_prompt,
            mcp_servers=mcp_servers,
            hooks=self._hooks if self._hooks else self._default_hooks(),
            permission_mode="bypassPermissions",
        )

    def _build_system_prompt(self) -> str:
        """Build the full system prompt."""
        # Get current date/time
        try:
            import pytz
            user_tz = pytz.timezone(os.getenv("USER_TIMEZONE", "America/Chicago"))
            user_now = datetime.now(user_tz)
        except ImportError:
            utc_now = datetime.now(timezone.utc)
            cst_offset = timezone(timedelta(hours=-6))
            user_now = utc_now.astimezone(cst_offset)

        today_str = user_now.strftime("%A, %B %d, %Y")
        tomorrow_str = (user_now + timedelta(days=1)).strftime("%A, %B %d, %Y")
        
        tool_knowledge_block = get_tool_knowledge_block()
        skills_section = f"\n   - Available skills (read SKILL.md for detailed instructions):\n{self._skills_xml}\n" if self._skills_xml else ""

        # Inject Soul if present
        soul_section = f"\n\n{self._soul_context}\n\n" if self._soul_context else ""

        return (
            f"{soul_section}"
            f"Current Date: {today_str}\n"
            f"Tomorrow is: {tomorrow_str}\n"
            f"{self._memory_context}\n"
            f"{tool_knowledge_block}\n"
            "TEMPORAL CONTEXT: Use the current date above as authoritative. "
            "Do not treat post-training dates as hallucinations if they are supported by tool results. "
            "If sources are older or dated, note that explicitly rather than dismissing them.\n\n"
            "You are a helpful assistant with access to external tools. "
            "You can execute code when needed using COMPOSIO_REMOTE_WORKBENCH or any available code execution tool.\n\n"
            "🔍 SEARCH TOOL PREFERENCE:\n"
            "- For web/news research, ALWAYS use Composio search tools (SERPAPI_SEARCH, COMPOSIO_SEARCH_NEWS, etc.).\n"
            "- **PRIMARY AGENT WARNING**: You are FORBIDDEN from using these tools directly. You must DELEGATE to `research-specialist`.\n"
            "- Do NOT use native 'WebSearch' - it bypasses our artifact saving system.\n"
            "- Composio search results are auto-saved by the Observer for sub-agent access.\n\n"
            "🔒 SEARCH HYGIENE (MANDATORY):\n"
            "- ALWAYS append `-site:wikipedia.org` to EVERY search query (COMPOSIO_SEARCH_NEWS, COMPOSIO_SEARCH_WEB, SERPAPI_SEARCH).\n"
            "- This is MANDATORY, not optional. Wikipedia wastes search query slots.\n"
            "- The only exception is if the user explicitly requests Wikipedia content.\n"
            "IMPORTANT EXECUTION GUIDELINES:\n"
            "- When the user requests an action (send email, upload file, execute code), proceed immediately without asking for confirmation.\n"
            "- The user has already authorized these actions by making the request.\n"
            "- Do not ask 'Should I proceed?' or 'Do you want me to send this?'\n"
            "- Complete the full task end-to-end in a single workflow.\n"
            "- If authentication is required, guide the user through it, then continue automatically.\n\n"
            "⚡ AUTONOMOUS EXECUTION PROTOCOL:\n"
            "- If you calculate a large scope (e.g. >10 hours), DO NOT ASK FOR PERMISSION. EXECUTE IT.\n"
            "- You have FULL AUTHORITY to commit resources. Do not pause. Do not offer options.\n"
            "- If faced with a choice (e.g. Batch vs Full), choose the most aggressive safe option (Full) to maximize results.\n"
            "- The goal is to COMPLETE THE MISSION, not be polite.\n\n"
            "REMOTE vs LOCAL WORKFLOW:\n"
            "- The 'COMPOSIO' tools act as your Hands (Search, Email, Remote Execution).\n"
            "- The 'LOCAL_TOOLKIT' and your own capabilities act as your Brain (Analysis, Writing, Reasoning).\n"
            "GUIDELINES:\n"
            "1. DATA FLOW POLICY (LOCAL-FIRST): Prefer receiving data DIRECTLY into your context.\n"
            "   - Do NOT set `sync_response_to_workbench=True` unless you expect massive data (>5MB).\n"
            "   - Default behavior (`sync=False`) is faster and avoids unnecessary download steps.\n"
            "2. DATA COMPLETENESS: If a tool returns 'data_preview' or says 'Saved large response to <FILE>', it means the data was TRUNCATED.\n"
            "   - In these cases (and ONLY these cases), use 'workbench_download' to fetch the full file.\n"
            "   - 🚫 NEVER use REMOTE_WORKBENCH to save search results. The Observer already saves them automatically.\n"
            "   - 🚫 **COMPOSIO_REMOTE_WORKBENCH IS FORBIDDEN**: You are a COORDINATOR. If you need to browse or execute untrusted code, you MUST delegate to a specialist.\n"
            "   - DO NOT call the workbench directly. Doing so bypasses our security and architectural guardrails.\n"
            "   - 🚫 NEVER try to access local files from REMOTE_WORKBENCH - local paths don't exist there!\n"
            "4. 🚨 MANDATORY DELEGATION FOR RESEARCH & REPORTS:\n"
            "   - Role: You are the COORDINATOR. You delegate work to specialists.\n"
            "   - 🚫 **ABSOLUTE PROHIBITION**: You must NOT perform web searches, crawls, or report writing yourself.\n"
            "   - You must delegate ALL research to `research-specialist` immediately.\n"
            "   - PROCEDURE:\n"
            "     1. **STEP 1:** Delegate to `research-specialist` using `Task` IMMEDIATELY.\n"
            "        PROMPT: 'Research [topic]: execute searches, crawl sources, finalize corpus.'\n"
            "        (The research-specialist handles: COMPOSIO search → crawl → filter → overview)\n"
            "     2. **STEP 2:** When Step 1 completes SUCCESSFULLY, delegate to `report-writer` using `Task`.\n"
            "        PROMPT: 'Write the full HTML report using refined_corpus.md.'\n"
            "     🚨 **CRITICAL**: If the research-specialist fails or returns an error (e.g., 'no results found'), DO NOT PROCEED to Step 2. Inform the user of the research failure instead.\n"
            "   - ✅ SubagentStop HOOK: When the sub-agent finishes, a hook will inject next steps.\n"
            "     Wait for this message before proceeding with upload/email.\n"
            "5. 📤 EMAIL ATTACHMENTS - USE `upload_to_composio` (ONE-STEP SOLUTION):\n"
            "   - For email attachments, call `mcp__local_toolkit__upload_to_composio(path='/local/path/to/file', session_id='xxx')`\n"
            "   - This tool handles EVERYTHING: local→remote→S3 in ONE call.\n"
            "   - It returns `s3_key` which you pass to GMAIL_SEND_EMAIL's `attachment.s3key` field.\n"
            "   - DO NOT manually call workbench_upload + REMOTE_WORKBENCH. That's the old, broken way.\n"
            "6. ⚠️ LOCAL vs REMOTE FILESYSTEM:\n"
            "   - LOCAL paths: `/home/kjdragan/...` or relative paths - accessible by local_toolkit tools.\n"
            "   - REMOTE paths: `/home/user/...` - only accessible inside COMPOSIO_REMOTE_WORKBENCH sandbox.\n"
            "7. 📁 WORK PRODUCTS (EPHEMERAL DATA):\n"
            "   - Definition: Intermediate task data, crawl results, search outputs, and draft findings.\n"
            "   - Location: ALWAYS save to `os.path.join(os.getenv('CURRENT_SESSION_WORKSPACE'), 'work_products', 'filename')`.\n"
            "   - Lifecycle: These directories are ephemeral and may be deleted. Do NOT save important code here.\n"
            "   - Mandatory Save: Save any significant tables, summaries, or analyses BEFORE responding to the user.\n\n"
            "8. 🏢 PROJECT HYGIENE & ASSET BOUNDARIES (PERSISTENT CODE):\n"
            "   🚨 MANDATORY: Prevent repository root clutter. Distinguish between 'Run Data' and 'Development Assets'.\n"
            "   - **Persistent Development**: Any core code, reusable scripts, or permanent documentation MUST be saved to their respective directories:\n"
            "     - Utility Scripts: `scripts/` (e.g., scripts/sync_memory.py)\n"
            "     - Core Logic: `src/universal_agent/` (or designated subpackages)\n"
            "     - Permanent Docs: `Project_Documentation/` or `docs/` (e.g., docs/new_feature_spec.md)\n"
            "   - **Root Protection**: DO NOT save new files to the repository root. Exceptions: README.md, .env, pyproject.toml.\n"
            "   - **Verification**: Before saving a file, ask: 'Is this a permanent part of the codebase or a temporary task artifact?'\n\n"
            "9. 🔗 MANDATORY DELEGATION FOR RESEARCH (NO EXCEPTIONS):\n"
            "   - **RESEARCH vs REPORT**: Distinguish between these two activities:\n"
            "     1. **RESEARCH** (Keywords: 'search', 'find', 'research', 'latest news'):\n"
            "        - Delegate ONLY to `research-specialist`.\n"
            "        - Result: A refined corpus and a summary provided in the chat.\n"
            "        - **DO NOT** trigger a formal report/PDF unless explicitly requested.\n"
            "     2. **REPORT** (Keywords: 'report', 'comprehensive document', 'formal presentation'):\n"
            "        - After research is complete, delegate to `report-writer` ONLY if the user explicitly asked for a 'report' or 'document'.\n"
            "   - **PROHIBITION**: Do NOT use `WebSearch` or search tools directly. Always delegate research first.\n"
            "   - **FLOW**: User Request -> `research-specialist` -> Summary to User -> (Optional) `report-writer` if requested.\n\n"
            "10. 💡 PROACTIVE FOLLOW-UP SUGGESTIONS:\n"
            "   - After completing a task, suggest 2-3 helpful follow-up actions based on what was just accomplished.\n"
            "   - Examples: 'Would you like me to email this report?', 'Should I save this to a different format?',\n"
            "     'I can schedule a calendar event for the mentioned deadline if you'd like.'\n"
            "   - Keep suggestions relevant to the completed task and the user's apparent goals.\n\n"
            "11. 🎯 SKILLS - BEST PRACTICES KNOWLEDGE:\n"
            "   - Skills are pre-defined workflows for complex tasks.\n"
            "   - Before building document creation scripts from scratch, CHECK if a skill exists.\n"
            f"{skills_section}"
        )

    def _build_mcp_servers(self) -> dict:
        """Build MCP servers configuration."""
        return {
            "composio": {
                "type": "http",
                "url": self._session.mcp.url,
                "headers": {"x-api-key": os.environ.get("COMPOSIO_API_KEY", "")},
            },
            "local_toolkit": {
                "type": "stdio",
                "command": sys.executable,
                "args": [
                    "-u",
                    os.path.join(
                        os.path.dirname(os.path.dirname(__file__)), "mcp_server.py"
                    )
                ],
                "env": {
                    "LOGFIRE_TOKEN": os.environ.get("LOGFIRE_TOKEN", ""),
                },
            },
            # "edgartools": {
            #     "type": "stdio",
            #     "command": sys.executable,
            #     "args": ["-m", "edgar.ai"],
            #     "env": {
            #         "EDGAR_IDENTITY": os.environ.get("EDGAR_IDENTITY", "Agent agent@example.com")
            #     },
            # },
            # "video_audio": {
            #     "type": "stdio",
            #     "command": sys.executable,
            #     "args": [
            #         os.path.join(
            #             os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            #             "external_mcps",
            #             "video-audio-mcp",
            #             "server.py",
            #         )
            #     ],
            # },
            # "youtube": {
            #     "type": "stdio",
            #     "command": sys.executable,
            #     "args": ["-m", "mcp_youtube"],
            # },
            "internal": create_sdk_mcp_server(
                name="internal",
                version="1.0.0",
                tools=[
                    run_report_generation_wrapper, 
                    run_research_pipeline_wrapper, 
                    crawl_parallel_wrapper, 
                    run_research_phase_wrapper,
                    generate_outline_wrapper,
                    draft_report_parallel_wrapper,
                    cleanup_report_wrapper,
                    compile_report_wrapper,
                ] + ([ua_memory_get] if self.enable_memory else [])
            ),
            "taskwarrior": {
                "type": "stdio",
                "command": sys.executable,
                "args": [
                    os.path.join(
                        os.path.dirname(__file__), "mcp_server_taskwarrior.py"
                    )
                ],
            },
            "telegram": {
                "type": "stdio",
                "command": sys.executable,
                "args": [
                    os.path.join(
                        os.path.dirname(__file__), "mcp_server_telegram.py"
                    )
                ],
                "env": {
                    "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
                    "TELEGRAM_ALLOWED_USER_IDS": os.environ.get("TELEGRAM_ALLOWED_USER_IDS", ""),
                },
            },
        }

    def _default_hooks(self) -> dict:
        """Return default hooks configuration."""
        # Import hooks from agent_core to avoid duplication
        try:
            from universal_agent.agent_core import (
                malformed_tool_guardrail_hook,
                tool_output_validator_hook,
                pre_compact_context_capture_hook,
            )
            from universal_agent.guardrails.tool_schema import pre_tool_use_schema_guardrail
            return {
                "PreToolUse": [
                    HookMatcher(matcher="*", hooks=[pre_tool_use_schema_guardrail]),
                    HookMatcher(matcher="*", hooks=[malformed_tool_guardrail_hook]),
                ],
                "PostToolUse": [
                    HookMatcher(matcher="Write", hooks=[tool_output_validator_hook]),
                ],
                "PreCompact": [
                    HookMatcher(matcher="*", hooks=[pre_compact_context_capture_hook]),
                ],
            }
        except ImportError:
            return {}

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            print(message, flush=True)


def create_workspace_path(base_dir: Optional[str] = None) -> str:
    """
    Create a new session workspace directory.
    
    Args:
        base_dir: Optional base directory. If not provided, auto-discovers.
        
    Returns:
        Absolute path to the created workspace directory.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if base_dir:
        workspace_dir = os.path.join(base_dir, f"session_{timestamp}")
    elif os.getenv("AGENT_WORKSPACE_ROOT"):
        workspace_dir = os.path.join(os.getenv("AGENT_WORKSPACE_ROOT"), f"session_{timestamp}")
    else:
        # Auto-discovery
        src_dir = _get_src_dir()
        for candidate in ["/app", src_dir, "/tmp"]:
            workspace_dir = os.path.join(candidate, "AGENT_RUN_WORKSPACES", f"session_{timestamp}")
            try:
                os.makedirs(workspace_dir, exist_ok=True)
                return workspace_dir
            except PermissionError:
                continue
        raise RuntimeError("Cannot create workspace directory in any location")
    
    os.makedirs(workspace_dir, exist_ok=True)
    return workspace_dir
