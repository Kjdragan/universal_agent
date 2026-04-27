"""
AgentSetup - Unified initialization for CLI, API, and URW harness.

This module provides a single source of truth for agent configuration,
ensuring consistent behavior across all entry points.
"""

import asyncio
from datetime import datetime
import os
from pathlib import Path
import sys
from typing import Any, Optional
import uuid

from claude_agent_sdk import create_sdk_mcp_server
from claude_agent_sdk.types import ClaudeAgentOptions, HookMatcher
from composio import Composio
import yaml

from universal_agent.agentmail_official import build_agentmail_mcp_server_config
from universal_agent.execution_context import bind_workspace_env
from universal_agent.feature_flags import (
    coder_vp_enabled,
    memory_enabled,
    memory_max_tokens,
)
from universal_agent.memory.paths import (
    resolve_shared_memory_workspace,
)
from universal_agent.notebooklm_runtime import build_notebooklm_mcp_server_config
from universal_agent.prompt_assets import (
    discover_skills,
    generate_skills_xml,
)
from universal_agent.prompt_builder import build_sdk_system_prompt, build_system_prompt
from universal_agent.runtime_bootstrap import bootstrap_runtime_environment
from universal_agent.runtime_role import resolve_factory_role
from universal_agent.sdk.runtime_info import emit_sdk_runtime_banner
from universal_agent.tools.internal_registry import get_all_internal_tools


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
        resolved_enable_memory = memory_enabled() if enable_memory is None else enable_memory
        self.enable_memory = resolved_enable_memory
        self.memory_max_tokens = memory_max_tokens()
        self.verbose = verbose
        
        # Factory Role assignment (finalized fallback policy lives in runtime_role)
        self.factory_role = resolve_factory_role().value
        self.enable_vp_coder = coder_vp_enabled()
        
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

        bootstrap_state = bootstrap_runtime_environment()
        self.factory_role = bootstrap_state.policy.role
        self.enable_vp_coder = coder_vp_enabled()
        runtime_info = emit_sdk_runtime_banner(required="0.1.48")
        self._log(
            "🔧 Claude Agent SDK runtime: "
            f"sdk={runtime_info.sdk_version}, bundled_cli={runtime_info.bundled_cli_version}"
        )

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

        # IMPORTANT: Composio sessions only expose tools for toolkits explicitly enabled for the session.
        # If we don't enable a connected toolkit (e.g. reddit), it can appear "disconnected" even when
        # connected accounts exist. Prefer enabling toolkits that are ACTIVE for this user.
        enabled_toolkits: list[str] = []
        try:
            connections = await asyncio.to_thread(
                self._composio.connected_accounts.list,
                user_ids=[self.user_id],
                limit=200,
            )
            items = getattr(connections, "items", []) or []
            active_slugs: set[str] = set()
            for item in items:
                toolkit = getattr(item, "toolkit", None)
                slug = getattr(toolkit, "slug", "") if toolkit else ""
                status = getattr(item, "status", "")
                if slug and status == "ACTIVE":
                    active_slugs.add(slug)

            # Ensure critical Composio-native toolkits are available for orchestration.
            active_slugs.update({"composio_search", "codeinterpreter"})

            blacklist = {"firecrawl", "exa", "jira", "semanticscholar"}
            enabled_toolkits = sorted(s for s in active_slugs if s not in blacklist)

            if enabled_toolkits:
                self._log(
                    f"🧩 Enabling {len(enabled_toolkits)} Composio toolkits for session (ACTIVE + core)."
                )
        except Exception as e:
            # Safe fallback: keep legacy behavior (default toolkit set) if discovery fails.
            self._log(f"⚠️ Failed to build enabled toolkit list; falling back to defaults: {e}")

        # If we have an explicit allowlist, use it. Otherwise preserve legacy behavior.
        toolkits_payload: dict = (
            {"enable": enabled_toolkits}
            if enabled_toolkits
            else {"disable": ["firecrawl", "exa", "jira", "semanticscholar"]}
        )
        session_future = asyncio.to_thread(
            self._composio.create,
            user_id=self.user_id,
            toolkits=toolkits_payload,
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
                fetch_toolkit_meta,
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
        """Load memory context for system prompt.

        Canonical source: shared workspace memory files/index for cross-session
        continuity.
        """
        try:
            from universal_agent.memory.memory_context import build_file_memory_context

            shared_memory_dir = resolve_shared_memory_workspace(self.workspace_dir)
            context = build_file_memory_context(
                shared_memory_dir,
                max_tokens=self.memory_max_tokens,
                index_mode="vector",
                recent_limit=int(os.getenv("UA_MEMORY_RECENT_ENTRIES", "8")),
            )
            self._log(f"🧠 Injected Core Memory Context ({len(context)} chars)")
            return context
        except Exception as e:
            self._log(f"⚠️ Failed to load Memory Context: {e}")
            return ""

    def _load_soul_context(self):
        """Load the 'Soul' (Persona/Identity) from SOUL.md."""
        # Priority 1: Run Workspace (Task-specific override)
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
        system_prompt_option, prompt_mode = build_sdk_system_prompt(system_prompt)
        self._log(f"🧾 System prompt mode: {prompt_mode}")
        
        # Build MCP servers config
        mcp_servers = self._build_mcp_servers()
        
        # Build disallowed tools list
        disallowed_tools = list(DISALLOWED_TOOLS)
        if not self.enable_memory:
            disallowed_tools.extend([
                "memory_get",
                "memory_search",
                "memory_get",
                "memory_search",
            ])

        from universal_agent.utils.model_resolution import (
            resolve_agent_teams_enabled,
            resolve_claude_code_model,
        )

        return ClaudeAgentOptions(
            model=resolve_claude_code_model(default="opus"),
            add_dirs=[os.path.join(self.src_dir, ".claude")],
            setting_sources=["project"],  # Enable loading agents from .claude/agents/
            disallowed_tools=disallowed_tools,
            env={
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS": os.getenv("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000"),
                "MAX_MCP_OUTPUT_TOKENS": os.getenv("MAX_MCP_OUTPUT_TOKENS", "64000"),
                "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
                if resolve_agent_teams_enabled(default=True)
                else "0",
                "UA_ENABLE_SDK_TYPED_TASK_EVENTS": os.getenv("UA_ENABLE_SDK_TYPED_TASK_EVENTS", "0"),
                "UA_ENABLE_SDK_SESSION_HISTORY": os.getenv("UA_ENABLE_SDK_SESSION_HISTORY", "0"),
                "UA_ENABLE_DYNAMIC_MCP": os.getenv("UA_ENABLE_DYNAMIC_MCP", "0"),
                "CURRENT_RUN_WORKSPACE": os.path.abspath(self.workspace_dir),
                # Legacy alias kept during the run-workspace cutover.
                "CURRENT_SESSION_WORKSPACE": os.path.abspath(self.workspace_dir),
                # Durable outputs should go here; the run workspace is scratch.
                "UA_ARTIFACTS_DIR": os.path.abspath(
                    os.getenv(
                        "UA_ARTIFACTS_DIR",
                        str((Path(__file__).resolve().parent.parent.parent / "artifacts")),
                    )
                ),
            },
            system_prompt=system_prompt_option,
            mcp_servers=mcp_servers,
            hooks=self._hooks if self._hooks else self._default_hooks(),
            permission_mode="bypassPermissions",
        )

    def _build_system_prompt(self, workspace_path: str, agents: Optional[dict] = None) -> str:
        """Build the main system prompt via the shared prompt_builder."""
        
        # Load the dynamic capabilities registry
        capabilities_content = ""
        try:
            # Prefer a per-workspace generated registry (avoid mutating repo assets at runtime).
            workspace_caps = os.path.join(workspace_path, "capabilities.md")
            assets_caps = os.path.join(self.src_dir, "src", "universal_agent", "prompt_assets", "capabilities.md")
            capabilities_path = workspace_caps if os.path.exists(workspace_caps) else assets_caps
            if os.path.exists(capabilities_path):
                with open(capabilities_path, "r", encoding="utf-8") as f:
                    capabilities_content = f.read()
        except Exception:
            capabilities_content = "Capabilities registry not found."

        # ── VP detection: use streamlined prompt for VP workers ────────
        # VP workers receive their soul (CODIE/ATLAS) seeded into the workspace.
        # Detect by checking the soul content for VP identity markers.
        is_vp_worker = any(
            marker in (self._soul_context or "")
            for marker in ("CODIE", "ATLAS", "VP Coder Agent", "VP General Agent")
        )

        if is_vp_worker:
            from universal_agent.prompt_builder import build_vp_system_prompt
            prompt = build_vp_system_prompt(
                workspace_path=workspace_path,
                soul_context=self._soul_context,
                memory_context=self._memory_context,
                capabilities_content=capabilities_content,
            )
            self._log(f"📦 VP system prompt built ({len(prompt)} chars, ~{len(prompt)//4} tokens)")
            return prompt

        prompt = build_system_prompt(
            workspace_path=workspace_path,
            soul_context=self._soul_context,
            memory_context=self._memory_context,
            capabilities_content=capabilities_content,
            skills_xml=self._skills_xml,
        )
        self._log(f"📦 System prompt built ({len(prompt)} chars, ~{len(prompt)//4} tokens)")
        return prompt

    def _build_mcp_servers(self) -> dict:
        """Build MCP servers configuration."""
        from universal_agent.services.gws_mcp_bridge import build_gws_mcp_server_config

        servers = {
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
                    # Infisical stores key as ZAI_API_KEY; npm package expects Z_AI_API_KEY
                    "Z_AI_API_KEY": os.environ.get("Z_AI_API_KEY") or os.environ.get("ZAI_API_KEY", ""),
                    "Z_AI_MODE": os.environ.get("Z_AI_MODE", "ZAI"),
                },
            },
        }

        # Google Workspace CLI MCP server (feature-gated)
        gws_config = build_gws_mcp_server_config()
        if gws_config is not None:
            servers["gws"] = gws_config

        # NotebookLM MCP server (feature-gated, default off for context budget)
        notebooklm_config = build_notebooklm_mcp_server_config()
        if notebooklm_config is not None:
            servers["notebooklm-mcp"] = notebooklm_config

        agentmail_config = build_agentmail_mcp_server_config()
        if agentmail_config is not None:
            servers["agentmail"] = agentmail_config

        return servers

    def _generate_capabilities_doc(self) -> None:
        """
        Generate a comprehensive capabilities registry (capabilities.md) in the workspace.
        Organizes agents and tools by DOMAIN to support the Universal Coordinator architecture.
        """
        try:
            lines = ["<!-- Agent Capabilities Registry -->", "", f"<!-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} -->", ""]
            
            lines.append(f"### 🏭 Local Factory Role: {self.factory_role}")
            lines.append(f"You are operating under the **{self.factory_role}** role. You must act strictly within the boundaries of this assignment.")
            lines.append("")
            
            # --- DOMAIN DEFINITIONS ---
            domains = {
                "🌐 Browser Operations": [
                    "bowser",
                    "playwright-bowser",
                    "claude-bowser",
                    "browserbase",
                    "playwright",
                    "chrome",
                    "live-chrome",
                    "cdp",
                ],
                "🔬 Research & Analysis": ["research-specialist", "arxiv-specialist", "trend-specialist", "csi-trend-analyst", "professor", "scribe", "wiki-maintainer"],
                "🎨 Creative & Media": ["image-expert", "video-creation-expert", "video-remotion-expert"],
                "⚙️ Engineering & Code": ["task-decomposer", "code-writer", "codeinterpreter", "github"],
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
            lines.append("")
            lines.append("Browser lane policy: Bowser-first (`claude-bowser-agent`, `playwright-bowser-agent`, `bowser-qa-agent`).")
            lines.append("Use Browserbase when Bowser is unavailable or cloud-browser behavior is explicitly required.")
            
            if self.enable_vp_coder:
                lines.append("\n#### 👑 VP Orchestration (Code & Engineering)")
                lines.append("- **vp_coder**: The VP Coder is a powerful sub-agent running its own isolated environment, capable of planning and executing comprehensive codebase tasks.")
                lines.append("  -> **CRITICAL**: Use the `vp_orchestration` skill to interact with the VP Coder using the `vp_*` specialized tools. Do NOT use standard `Task()` routing.")
                lines.append("")
            
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
            
            lines.append("> *To assign a workflow, dispatch `Task(subagent_type='<name>', ...)`*")
            for domain, agents in agents_by_domain.items():
                lines.append(f"\n#### {domain}")
                for name, desc in agents:
                    clean_desc = desc.replace('\n', ' ').strip()
                    lines.append(f"- **{name}**: {clean_desc}")

            # Ensure system-configuration-agent guidance is always explicit in the registry.
            if "system-configuration-agent" in found_agents:
                lines.append("\n#### 🛠 Mandatory System Operations Routing")
                lines.append("- **system-configuration-agent**: Platform/runtime operations specialist for Chron scheduling, heartbeat, and ops config.")
                lines.append("  -> Delegate immediately for schedule and runtime parameter changes: `Task(subagent_type='system-configuration-agent', prompt='...')`")
                lines.append("- Do not use OS-level crontab for product scheduling requests; use Chron APIs and runtime config paths.")

            lines.append("")

            # 2. SKILLS (Standard Operating Procedures)
            lines.append("### 📚 Standard Operating Procedures (Skills)")
            lines.append("These guides represent the collective knowledge of the system.")
            lines.append("Prioritize using these highly-optimized procedures instead of improvising.")
            lines.append("")
            lines.append("**Progressive Disclosure**:")
            lines.append("1. **Scan**: Read the high-level index below to identify relevant skills.")
            lines.append("2. **Read**: If a skill seems useful, use `read_file` to read the full SKILL.md content.")
            lines.append("3. **Execute**: Follow the procedure step-by-step.")
            lines.append("")
            
            if self.enable_skills and self._discovered_skills:
                # Sort skills by name
                sorted_skills = sorted(self._discovered_skills, key=lambda x: x["name"])
                
                for skill in sorted_skills:
                    name = skill["name"]
                    desc = skill["description"].replace('\n', ' ').strip()
                    path = skill["path"]
                    is_enabled = skill.get("enabled", True)
                    
                    if not is_enabled:
                        reason = skill.get("disabled_reason", "Missing requirements")
                        lines.append(f"- ~~**{name}**~~ (Unavailable: {reason})")
                        continue

                    lines.append(f"- **{name}** (`{path}`): {desc}")
            else:
                lines.append("- No skills discovered.")
            lines.append("")

            # 3b. TOOLKITS (By Domain)
            lines.append("### 🛠 Toolkits & Capabilities")
            
            # Combine Core + Connected for sorting
            all_apps = []
            core_slugs = {"composio_search", "browserbase", "browserbase_tool", "codeinterpreter", "filetool", "sqltool"}
            
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
            output_path = os.path.join(self.workspace_dir, "capabilities.md")
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
                pre_compact_context_capture_hook,
                tool_output_validator_hook,
            )
            from universal_agent.guardrails.tool_schema import (
                pre_tool_use_schema_guardrail,
            )
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
    Create a new durable run workspace directory.
    
    Args:
        base_dir: Optional base directory. If not provided, auto-discovers.
        
    Returns:
        Absolute path to the created workspace directory.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    workspace_name = f"run_{timestamp}"
    
    if base_dir:
        workspace_dir = os.path.join(base_dir, workspace_name)
    elif os.getenv("AGENT_WORKSPACE_ROOT"):
        workspace_dir = os.path.join(os.getenv("AGENT_WORKSPACE_ROOT"), workspace_name)
    else:
        # Auto-discovery
        src_dir = _get_src_dir()
        for candidate in ["/app", src_dir, "/tmp"]:
            workspace_dir = os.path.join(candidate, "AGENT_RUN_WORKSPACES", workspace_name)
            try:
                os.makedirs(workspace_dir, exist_ok=True)
                return workspace_dir
            except PermissionError:
                continue
        raise RuntimeError("Cannot create workspace directory in any location")
    
    os.makedirs(workspace_dir, exist_ok=True)
    return workspace_dir
