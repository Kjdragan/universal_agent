from datetime import datetime
import os
import sys

import yaml

# 1. Setup paths so imports work without PYTHONPATH
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src_dir = os.path.join(project_root, "src")
if os.path.exists(src_dir) and src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from universal_agent.prompt_assets import discover_skills


def main():
    print("🚀 Generating Capabilities Manually...")
    
    target_path = os.path.join(src_dir, "universal_agent", "prompt_assets", "capabilities.md")
    print(f"Target: {target_path}")
    
    lines = [
        "<!-- Agent Capabilities Registry -->",
        "",
        f"<!-- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} -->",
        "",
        "# 🧠 Agent Capabilities Registry",
        "",
        "### 🧭 Capability Routing Doctrine",
        "- Evaluate multiple capability lanes before selecting an execution path for non-trivial tasks.",
        "- Do not default to research/report unless explicitly requested or clearly required.",
        "- Browser tasks are Bowser-first: `claude-bowser-agent` (identity/session), `playwright-bowser-agent` (parallel/repeatable), `bowser-qa-agent` (UI validation).",
        "- Use `browserbase` when Bowser lanes are unavailable or cloud-browser behavior is explicitly needed.",
        "",
        "### 🔍 Decomposing Research Requests",
        "The term 'research' is broad. You must decompose the user's intent and select the appropriate specialist:",
        "- **General Web & News Research**: For finding articles, scraping sites, and building standard knowledge corporas, delegate to `research-specialist`.",
        "- **Audio & Synthesis (NotebookLM)**: If the request involves generating podcasts, audio overviews, slide decks, or deep study guides, delegate to `notebooklm-operator` or use the `notebooklm-orchestration` skill.",
        "- **Video Transcripts (YouTube)**: If the research requires analyzing YouTube content, delegate to `youtube-expert`.",
        "Do not default blindly to one specialist. Chain them if required (e.g., use `research-specialist` to find URLs, then `notebooklm-operator` to synthesize them into a podcast).",
        "",
        "### 📄 Report & PDF Workflow (Built-in MCP Tools)",
        "When the user requests reports, PDFs, or email delivery of documents:",
        "- **Research phase**: Use `mcp__internal__run_research_pipeline` or dispatch `Task(subagent_type='research-specialist', ...)` to gather data into task corpus files.",
        "- **Report generation**: Use `mcp__internal__run_report_generation(task_name='<task>')` to delegate to the Report Writer sub-agent which handles outline → draft → cleanup → compile → PDF automatically.",
        "- **HTML → PDF conversion**: Use `mcp__internal__html_to_pdf(html_path='<path>', output_path='<path>.pdf')`. Do NOT use Bash with chrome/wkhtmltopdf/weasyprint — the MCP tool handles fallback automatically.",
        "- **Multiple reports**: Call `run_report_generation` once per topic, or write HTML via Write tool then convert each with `html_to_pdf`.",
        "- **Email with attachments**: Use gws Gmail send tool with local file path as attachment (no upload step needed). For non-Gmail delivery, use `mcp__internal__upload_to_composio` first.",
        "",
        "### 🏭 External VP Control Plane",
        "- For user requests that explicitly mention General/Coder VP delegation, route directly through internal `vp_*` tools.",
        "- Primary lifecycle: `vp_dispatch_mission` -> `vp_wait_mission` -> `vp_get_mission`.",
        "- Use `vp_read_result_artifacts` to summarize VP outputs from workspace URIs.",
        "- Never wrap `vp_*` tools inside Composio multi-execute.",
        "",
    ]
    
    # Domains
    domains = {
        "🌐 Browser Operations": [
            "bowser",
            "playwright-bowser",
            "claude-bowser",
            "browserbase",
            "playwright",
            "chrome",
        ],
        "🔬 Research & Analysis": ["research-specialist", "trend-specialist", "csi-trend-analyst", "professor", "scribe", "notebooklm-operator"],
        "🎨 Creative & Media": ["image-expert", "video-creation-expert", "video-remotion-expert", "youtube-expert"],
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
    
    def get_domain(name: str) -> str:
        name_lower = name.lower()
        for domain, keywords in domains.items():
            if any(k in name_lower for k in keywords):
                return domain
        return "🛠 General Tools"

    # Specialist Agents
    lines.append("## 🤖 Specialist Agents (Micro-Agents)")
    lines.append("Delegate full workflows to these specialists based on value-add.")
    lines.append("")
    
    agent_dirs = [
        os.path.join(project_root, ".claude", "agents"),
        os.path.join(src_dir, "universal_agent", "agent_college"),
    ]
    
    found_agents = {}
    
    for directory in agent_dirs:
        if not os.path.exists(directory):
            continue
        for filename in sorted(os.listdir(directory)):
            if filename.endswith(".md") or filename.endswith(".py"):
                if filename.startswith("_") or filename == "common.py": 
                    continue
                    
                filepath = os.path.join(directory, filename)
                name = filename.replace(".md", "").replace(".py", "")
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
                     if name == "professor": description = "Academic oversight and skill creation."
                     if name == "scribe": description = "Memory logging and fact recording."

                # Clean up description
                description = " ".join(str(description).split())
                found_agents[name] = description

    agents_by_domain: dict[str, list[tuple[str, str]]] = {}
    for name, desc in found_agents.items():
        domain = get_domain(name)
        if domain not in agents_by_domain:
            agents_by_domain[domain] = []
        agents_by_domain[domain].append((name, desc))
    
    for domain in sorted(agents_by_domain.keys()):
        lines.append(f"\n### {domain}")
        for name, desc in sorted(agents_by_domain[domain], key=lambda x: x[0].lower()):
            lines.append(f"- **{name}**: {desc}")
            lines.append(f"  -> Delegate: `Task(subagent_type='{name}', ...)`")

    if "system-configuration-agent" in found_agents:
        lines.append("\n### 🛠 Mandatory System Operations Routing")
        lines.append("- **system-configuration-agent**: Platform/runtime operations specialist for Chron scheduling, heartbeat, and ops config.")
        lines.append("  -> Delegate immediately for schedule and runtime parameter changes:")
        lines.append("  `Task(subagent_type='system-configuration-agent', prompt='Apply this system change safely and verify it.')`")
        lines.append("- Do not use OS-level crontab for product scheduling requests; use Chron APIs and runtime config paths.")

    lines.append("")

    # SKILLS
    lines.append("## 📚 Standard Operating Procedures (Skills)")
    lines.append("These organized guides are available to **ALL** agents and sub-agents. You should prioritize using these instead of improvising.")
    lines.append("They represent the collective knowledge of the system. **Think about your capabilities** and how these guides can help you.")
    lines.append("")
    lines.append("**Progressive Disclosure**:")
    lines.append("1. **Scan**: Read the YAML frontmatter below to identifying relevant skills.")
    lines.append("2. **Read**: If a skill seems useful, use `view_file` to read the full Markdown content (SOP).")
    lines.append("3. **Execute**: Follow the procedure step-by-step.")
    lines.append("")
    
    print("Discovering skills...")
    try:
        skills = discover_skills(os.path.join(project_root, ".claude", "skills"))
    except Exception as e:
        print(f"Error discovering skills: {e}")
        skills = []
        
    print(f"Found {len(skills)} skills")
    
    if skills:
        sorted_skills = sorted(skills, key=lambda x: x.get("name", "").lower())
        for skill in sorted_skills:
            name = skill.get("name", "unknown")
            desc = " ".join(str(skill.get("description", "No description")).split())
            path = skill.get("path", "")
            is_enabled = skill.get("enabled", True)
            
            if not is_enabled:
                reason = skill.get("disabled_reason", "Missing requirements")
                lines.append(f"### ~~{name}~~ (Unavailable)")
                lines.append(f"> **Reason**: {reason}")
                continue

            lines.append(f"### {name}")
            lines.append(f"{desc}")
            lines.append(f"Source: `{path}`")
            
            frontmatter = skill.get("frontmatter", {})
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
    
    # Toolkits (Simplified)
    lines.append("## 🛠 Toolkits & Capabilities")
    lines.append("- Core: Gmail, Calendar, Sheets, Docs, GitHub, Slack, Notion")
    lines.append("- Discovery: Run `mcp__composio__get_actions` to find more tools.")
    
    # Write
    with open(target_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
        
    print("✅ Done!")

if __name__ == "__main__":
    main()
