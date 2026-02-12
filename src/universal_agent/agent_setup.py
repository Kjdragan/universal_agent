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
import yaml

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
from universal_agent.tools.local_toolkit_bridge import (
    upload_to_composio_wrapper,
    list_directory_wrapper,
    append_to_file_wrapper,
    write_text_file_wrapper,
    finalize_research_wrapper,
    generate_image_wrapper,
    describe_image_wrapper,
    preview_image_wrapper,
    core_memory_replace_wrapper,
    core_memory_append_wrapper,
    archival_memory_insert_wrapper,
    archival_memory_search_wrapper,
    get_core_memory_blocks_wrapper,
    ask_user_questions_wrapper,
    batch_tool_execute_wrapper,
)
from universal_agent.tools.pdf_bridge import html_to_pdf_wrapper
from universal_agent.tools.memory import ua_memory_get_wrapper, ua_memory_search_wrapper
from universal_agent.tools.internal_registry import get_all_internal_tools
from universal_agent.execution_context import bind_workspace_env
from universal_agent.feature_flags import (
    memory_enabled,
    memory_index_mode,
    memory_max_tokens,
)


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
        enable_memory: Optional[bool] = None,
        verbose: bool = True,
    ):
        print(f"DEBUG: AgentSetup.__init__ called! enable_skills={enable_skills}")
        self.workspace_dir = workspace_dir
        from universal_agent.identity.resolver import resolve_user_id
        self.workspace_dir = workspace_dir
        self.user_id = resolve_user_id(user_id)
        self.enable_skills = enable_skills
        disable_memory = os.getenv("UA_DISABLE_LOCAL_MEMORY", "").lower() in {"1", "true", "yes"}
        resolved_enable_memory = memory_enabled() if enable_memory is None else enable_memory
        self.enable_memory = resolved_enable_memory and not disable_memory
        self.memory_index_mode = memory_index_mode()
        self.memory_max_tokens = memory_max_tokens()
        self.verbose = verbose
        
        self.run_id = str(uuid.uuid4())
        self.src_dir = _get_src_dir()
        
        # Initialized by initialize()
        self._composio: Optional[Composio] = None
        self._session: Optional[Any] = None
        self._options: Optional[ClaudeAgentOptions] = None
        self._initialized = False
        
        # Cached discovery results
        self._discovered_apps: list[dict] = []
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
            toolkits={"disable": ["firecrawl", "exa", "jira", "semanticscholar"]},
        )

        self._log("⏳ Discovering connected apps...")
        discovery_future = self._discover_apps_async()

        # Await session first (critical path)
        self._session = await session_future
        self._log("✅ Composio Session Created")

        # Await discovery
        self._discovered_apps = await discovery_future
        
        # Ensure discovery didn't fail/return None
        if not self._discovered_apps:
            # If discovery failed entirely, we might want a minimal fallback, 
            # but _discover_apps_async already catches exceptions and returns []
            # We could add hardcoded core apps here as a last resort if needed, 
            # but for now let's rely on the updated discovery logic.
            self._discovered_apps = []

        self._log(f"✅ Active Apps: {[app['slug'] for app in self._discovered_apps]}")

        # Discover skills
        if self.enable_skills:
            self._log("🔍 Attempting to discover skills...")
            self._discovered_skills = discover_skills()
            self._log(f"🔍 discover_skills() returned {len(self._discovered_skills)} items")
            skill_names = [s["name"] for s in self._discovered_skills]
            self._log(f"✅ Discovered Skills: {skill_names}")
            self._skills_xml = generate_skills_xml(self._discovered_skills)
        else:
            self._log("⚠️ Skills discovery DISABLED via enable_skills=False")

        # Load memory context
        if self.enable_memory:
            self._memory_context = self._load_memory_context()
        
        # Load soul/persona
        self._load_soul_context()
        
        # Generate capabilities registry (BEFORE building options/prompt)
        self._generate_capabilities_doc()
        
        # Build options
        self._options = self._build_options()
        self._initialized = True

    async def _discover_apps_async(self) -> list[dict]:
        """Discover connected Composio apps with metadata."""
        try:
            from universal_agent.utils.composio_discovery import (
                discover_connected_toolkits_with_meta,
                fetch_toolkit_meta
            )
            
            # 1. Discover connected apps
            connected_apps = await asyncio.to_thread(
                discover_connected_toolkits_with_meta, self._composio, self.user_id
            )
            
            # 2. Add core apps if missing, fetching their metadata
            # These are "Standard toolkits" we always want the agent to know are available.
            core_apps = [
                "gmail", "googlecalendar", "googlesheets", "googledocs", 
                "github", "slack", "notion", "discord", "reddit", "telegram",
                "figma", "composio_search", 
                "browserbase", "codeinterpreter"
            ]
            connected_slugs = {app['slug'] for app in connected_apps}
            
            for app_slug in core_apps:
                if app_slug not in connected_slugs:
                    # Fetch metadata for core app even if not connected
                    meta = await asyncio.to_thread(
                        fetch_toolkit_meta, self._composio, app_slug
                    )
                    connected_apps.append(meta)
                    
            return connected_apps
            
        except Exception as e:
            self._log(f"⚠️ App discovery failed: {e}")
            return []

    def _load_memory_context(self) -> str:
        """Load memory context for system prompt."""
        try:
            from Memory_System.manager import MemoryManager
            from universal_agent.agent_college.integration import setup_agent_college
            from universal_agent.memory.memory_context import build_file_memory_context

            storage_path = os.getenv(
                "PERSIST_DIRECTORY", os.path.join(self.src_dir, "Memory_System_Data")
            )
            mem_mgr = MemoryManager(storage_dir=storage_path, workspace_dir=self.workspace_dir)
            setup_agent_college(mem_mgr)
            context = mem_mgr.get_system_prompt_addition()
            file_context = build_file_memory_context(
                self.workspace_dir,
                max_tokens=self.memory_max_tokens,
                index_mode=self.memory_index_mode,
                recent_limit=int(os.getenv("UA_MEMORY_RECENT_ENTRIES", "8")),
            )
            if file_context:
                context = f"{context}\n{file_context}\n"
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
        if self.enable_memory:
            self._memory_context = self._load_memory_context()
        
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
            from universal_agent.memory.memory_store import ensure_memory_scaffold
            ensure_memory_scaffold(self.workspace_dir)

    def _build_options(self) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions with full configuration."""
        # Build system prompt
        system_prompt = self._build_system_prompt(self.workspace_dir)
        
        # Build MCP servers config
        mcp_servers = self._build_mcp_servers()
        
        # Build disallowed tools list
        disallowed_tools = list(DISALLOWED_TOOLS)
        if not self.enable_memory:
            disallowed_tools.extend([
                "mcp__internal__core_memory_replace",
                "mcp__internal__core_memory_append",
                "mcp__internal__archival_memory_insert",
                "mcp__internal__archival_memory_search",
                "mcp__internal__get_core_memory_blocks",
            ])

        return ClaudeAgentOptions(
            model=(
                (os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL") or "").strip()
                or (os.getenv("MODEL_NAME") or "").strip()
                or "glm-5"
            ),
            add_dirs=[os.path.join(self.src_dir, ".claude")],
            setting_sources=["project"],  # Enable loading agents from .claude/agents/
            disallowed_tools=disallowed_tools,
            env={
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": os.getenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000"),
                "MAX_MCP_OUTPUT_TOKENS": os.getenv("MAX_MCP_OUTPUT_TOKENS", "64000"),
                "CURRENT_SESSION_WORKSPACE": os.path.abspath(self.workspace_dir),
                # Durable outputs should go here; session workspace is scratch.
                "UA_ARTIFACTS_DIR": os.path.abspath(
                    os.getenv(
                        "UA_ARTIFACTS_DIR",
                        str((Path(__file__).resolve().parent.parent.parent / "artifacts")),
                    )
                ),
            },
            system_prompt=system_prompt,
            mcp_servers=mcp_servers,
            hooks=self._hooks if self._hooks else self._default_hooks(),
            permission_mode="bypassPermissions",
        )

    def _build_system_prompt(self, workspace_path: str, agents: Optional[dict] = None) -> str:
        """Build the main system prompt."""
        
        # Load the dynamic capabilities registry
        capabilities_content = ""
        try:
            capabilities_path = os.path.join(self.src_dir, "src", "universal_agent", "prompt_assets", "capabilities.md")
            if os.path.exists(capabilities_path):
                with open(capabilities_path, "r", encoding="utf-8") as f:
                    capabilities_content = f.read()
        except Exception:
            capabilities_content = "Capabilities registry not found."

        # Get temporal context
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
        temporal_line = f"Current Date: {today_str}\nTomorrow is: {tomorrow_str}"
        
        prompt = (
            f"{temporal_line}\n"
            "You are the **Universal Coordinator Agent**. You are a helpful, capable, and autonomous AI assistant.\n\n"
            "## 🧠 YOUR CAPABILITIES & SPECIALISTS\n"
            "You are not alone. You have access to a team of **Specialist Agents** and **Toolkits** organized by DOMAIN.\n"
            "Your primary job is to **Route Work** to the best specialist for the task.\n\n"
            f"{capabilities_content}\n\n"
            "## 🏗️ ARCHITECTURE & TOOL USAGE\n"
            "You interact with external tools via MCP tool calls. You do NOT write Python/Bash code to call SDKs directly.\n"
            "**Tool Namespaces:**\n"
            "- `mcp__composio__*` - Remote tools (Gmail, Slack, Search) -> Call directly\n"
            "- `mcp__internal__*` - Local tools (File I/O, Memory) -> Call directly\n"
            "- `Task` - **DELEGATION TOOL** -> Use this to hand off work to Specialist Agents.\n\n"
            "## 🚀 EXECUTION STRATEGY (THE COORDINATOR LOOP)\n"
            "1. **Analyze Request**: What DOMAIN does this fall into? (Research? Coding? Creative? Ops?)\n"
            "2. **Check Registry**: Look at the 'Specialist Agents' list above. Is there an expert for this?\n"
            "   - Need deep research? -> Delegate to `research-specialist`.\n"
            "   - Need a video? -> Delegate to `video-creation-expert`.\n"
            "   - Need complex coding? -> Delegate to `task-decomposer` or `codeinterpreter`.\n"
            "   - Need to check Slack/Email? -> Delegate to `slack-expert` or use tools directly.\n"
            "3. **Delegate**: Use `Task(subagent_type='[name]', ...)` to hand off the workflow.\n"
            "4. **Fallback**: If NO specialist exists, use your own tools (`read_file`, `write_file`, `bash`, etc.) to solve it.\n\n"
            "🛑 **CRITICAL RULE**: Do not attempt complex multi-step workflows (like 'Research & Report' or 'Video Production') yourself if a Specialist exists.\n"
            "**Your Value**: You obtain the user's intent, route it to the right expert, and synthesize the result.\n\n"
            "## ⚡ AUTONOMOUS BEHAVIOR\n"
            "- **Proactive**: If a task requires multiple steps (search -> summarize -> email), plan and execute the chain.\n"
            "- **Filesystem**: `CURRENT_SESSION_WORKSPACE` is your scratchpad. `UA_ARTIFACTS_DIR` is for permanent output.\n"
            "- **Safety**: Always use absolute paths. Do not access files outside your workspace.\n\n"
            "## 📧 EMAIL & COMMUNICATION\n"
            "- When sending emails, use `mcp__internal__upload_to_composio` to handle attachments.\n"
            "- Keep email bodies concise. Delegate drafting to the `scribe` or `writer` if needed.\n\n"
            f"Context:\nCURRENT_SESSION_WORKSPACE: {workspace_path}\n"
        )
        return prompt

    def _build_mcp_servers(self) -> dict:
        """Build MCP servers configuration."""
        return {
            "composio": {
                "type": "http",
                "url": self._session.mcp.url,
                "headers": {"x-api-key": os.environ.get("COMPOSIO_API_KEY", "")},
            },
            # local_toolkit subprocess disabled in favor of in-process tools
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
                tools=get_all_internal_tools(self.enable_memory)
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
            # External MCP: Z.AI Vision (GLM-4.6V) for image/video analysis (optional)
            "zai_vision": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@z_ai/mcp-server"],
                "env": {
                    "Z_AI_API_KEY": os.environ.get("Z_AI_API_KEY", ""),
                    "Z_AI_MODE": os.environ.get("Z_AI_MODE", "ZAI"),
                },
            },
        }

    def _generate_capabilities_doc(self) -> None:
        """
        Generate a comprehensive capabilities registry (capabilities.md) in the workspace.
        Organizes agents and tools by DOMAIN to support the Universal Coordinator architecture.
        """
        try:
            lines = ["<!-- Agent Capabilities Registry -->", "", f"<!-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} -->", ""]
            
            # --- DOMAIN DEFINITIONS ---
            domains = {
                "🔬 Research & Analysis": ["research-specialist", "trend-specialist", "professor", "scribe"],
                "🎨 Creative & Media": ["image-expert", "video-creation-expert", "video-remotion-expert"],
                "⚙️ Engineering & Code": ["task-decomposer", "codeinterpreter", "github"],
                "🏢 Operations & Communication": [
                    "slack-expert",
                    "gmail",
                    "googlecalendar",
                    "notion",
                    "linear",
                    "system-configuration-agent",
                    "ops",
                    "heartbeat",
                    "chron",
                    "cron",
                ],
            }
            
            # helper to find domain for an item
            def get_domain(name: str) -> str:
                name_lower = name.lower()
                for domain, keywords in domains.items():
                    if any(k in name_lower for k in keywords):
                        return domain
                return "🛠 General Tools"

            # 1. SPECIALIST AGENTS (Categorized)
            lines.append("### 🤖 Specialist Agents (Micro-Agents)")
            lines.append("Delegate full workflows to these specialists based on value-add.")
            
            agent_dirs = [
                os.path.join(self.src_dir, ".claude", "agents"),
                os.path.join(self.src_dir, "src", "universal_agent", "agent_college"),
            ]
            
            found_agents = {} # name -> description
            
            for directory in agent_dirs:
                if not os.path.exists(directory):
                    continue
                for filename in sorted(os.listdir(directory)):
                    if filename.endswith(".md") or filename.endswith(".py"):
                        # Skip __init__.py and common.py
                        if filename.startswith("_") or filename == "common.py": 
                            continue
                            
                        filepath = os.path.join(directory, filename)
                        name = filename.replace(".md", "").replace(".py", "")
                        
                        # Python agents (College) - simple heuristic for now
                        description = "Internal specialized agent."
                        
                        if filename.endswith(".md"):
                            try:
                                with open(filepath, "r", encoding="utf-8") as f:
                                    content = f.read()
                                if content.startswith("---"):
                                    parts = content.split("---", 2)
                                    if len(parts) >= 3:
                                        frontmatter = yaml.safe_load(parts[1])
                                        name = frontmatter.get("name") or name
                                        description = frontmatter.get("description", description)
                            except Exception:
                                pass
                        elif filename.endswith(".py"):
                             # Basic description for known college agents
                             if name == "professor": description = "Academic oversight and skill creation."
                             if name == "scribe": description = "Memory logging and fact recording."

                        found_agents[name] = description

            # Group by Domain
            agents_by_domain = {}
            for name, desc in found_agents.items():
                domain = get_domain(name)
                if domain not in agents_by_domain:
                    agents_by_domain[domain] = []
                agents_by_domain[domain].append((name, desc))
            
            for domain, agents in agents_by_domain.items():
                lines.append(f"\n#### {domain}")
                for name, desc in agents:
                    lines.append(f"- **{name}**: {desc}")
                    lines.append(f"  -> Delegate: `Task(subagent_type='{name}', ...)`")

            # Ensure system-configuration-agent guidance is always explicit in the registry.
            if "system-configuration-agent" in found_agents:
                lines.append("\n#### 🛠 Mandatory System Operations Routing")
                lines.append("- **system-configuration-agent**: Platform/runtime operations specialist for Chron scheduling, heartbeat, and ops config.")
                lines.append("  -> Delegate immediately for schedule and runtime parameter changes:")
                lines.append("  `Task(subagent_type='system-configuration-agent', prompt='Apply this system change safely and verify it.')`")
                lines.append("- Do not use OS-level crontab for product scheduling requests; use Chron APIs and runtime config paths.")

            lines.append("")

            # 2. SKILLS (Standard Operating Procedures)
            lines.append("### 📚 Standard Operating Procedures (Skills)")
            lines.append("These organized guides are available to **ALL** agents and sub-agents. You should prioritize using these instead of improvising.")
            lines.append("They represent the collective knowledge of the system. **Think about your capabilities** and how these guides can help you.")
            lines.append("")
            lines.append("**Progressive Disclosure**:")
            lines.append("1. **Scan**: Read the YAML frontmatter below to identifying relevant skills.")
            lines.append("2. **Read**: If a skill seems useful, use `mcp__internal__read_file` to read the full Markdown content (SOP).")
            lines.append("3. **Execute**: Follow the procedure step-by-step.")
            lines.append("")
            
            if self.enable_skills and self._discovered_skills:
                # Sort skills by name
                sorted_skills = sorted(self._discovered_skills, key=lambda x: x["name"])
                
                for skill in sorted_skills:
                    name = skill["name"]
                    desc = skill["description"]
                    path = skill["path"]
                    is_enabled = skill.get("enabled", True)
                    
                    if not is_enabled:
                        reason = skill.get("disabled_reason", "Missing requirements")
                        lines.append(f"#### ~~{name}~~ (Unavailable)")
                        lines.append(f"> **Reason**: {reason}")
                        continue

                    lines.append(f"#### {name}")
                    lines.append(f"{desc}")
                    lines.append(f"Source: `{path}`")
                    
                    # Dump Frontmatter to YAML block
                    frontmatter = skill.get("frontmatter", {})
                    # Clean up description from frontmatter if it's long, or just dump all
                    try:
                        yaml_str = yaml.dump(frontmatter, default_flow_style=False, sort_keys=False).strip()
                        lines.append("```yaml")
                        lines.append(yaml_str)
                        lines.append("```")
                    except Exception:
                        pass
                    lines.append("")
            else:
                lines.append("- No skills discovered.")
            lines.append("")

            # 3. TOOLKITS (By Domain)
            lines.append("### 🛠 Toolkits & Capabilities")
            
            # Combine Core + Connected for sorting
            all_apps = []
            core_slugs = {"composio_search", "browserbase", "codeinterpreter", "filetool", "sqltool"}
            
            if self._discovered_apps:
                all_apps.extend(self._discovered_apps)
            
            # Group Apps by Domain
            apps_by_domain = {}
            for app in all_apps:
                slug = app['slug']
                name = app.get('name', slug.title())
                desc = app.get('description', '')
                domain = get_domain(slug) # Use slug for matching
                
                if domain not in apps_by_domain:
                    apps_by_domain[domain] = []
                apps_by_domain[domain].append(f"- **{name}** (`{slug}`): {desc}")

            for domain, app_lines in apps_by_domain.items():
                lines.append(f"\n#### {domain}")
                lines.extend(app_lines)
                
            # Write file
            output_path = os.path.join(self.src_dir, "src", "universal_agent", "prompt_assets", "capabilities.md")
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            
            self._log(f"✅ Generated Domain-Driven Capabilities: {output_path}")

        except Exception as e:
            self._log(f"⚠️ Failed to generate capabilities.md: {e}")

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
