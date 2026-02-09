
import os
import sys
import yaml
from datetime import datetime
from universal_agent.prompt_assets import discover_skills

def main():
    print("🚀 Generating Capabilities Manually...")
    
    # 1. Setup paths
    # Assuming script run from project root, or we find it
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # lrepos/universal_agent/scripts/.. -> lrepos/universal_agent/
    if not os.path.exists(os.path.join(project_root, "src")):
        project_root = os.getcwd()
        
    src_dir = project_root
    target_path = os.path.join(src_dir, "src", "universal_agent", "prompt_assets", "capabilities.md")
    
    print(f"Target: {target_path}")
    
    # 2. Replicate Logic
    lines = ["# 🧠 Agent Capabilities Registry", "", f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
    
    # Domains
    domains = {
        "🔬 Research & Analysis": ["research-specialist", "trend-specialist", "professor", "scribe"],
        "🎨 Creative & Media": ["image-expert", "video-creation-expert", "video-remotion-expert"],
        "⚙️ Engineering & Code": ["task-decomposer", "codeinterpreter", "github"],
        "🏢 Operations & Communication": ["slack-expert", "gmail", "googlecalendar", "notion", "linear"]
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
    
    agent_dirs = [
        os.path.join(src_dir, ".claude", "agents"),
        os.path.join(src_dir, "src", "universal_agent", "agent_college"),
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

                found_agents[name] = description

    agents_by_domain = {}
    for name, desc in found_agents.items():
        domain = get_domain(name)
        if domain not in agents_by_domain:
            agents_by_domain[domain] = []
        agents_by_domain[domain].append((name, desc))
    
    for domain, agents in agents_by_domain.items():
        lines.append(f"\n### {domain}")
        for name, desc in agents:
            lines.append(f"- **{name}**: {desc}")
            lines.append(f"  -> Delegate: `Task(subagent_type='{name}', ...)`")

    lines.append("")

    # SKILLS (The fix)
    lines.append("## 📚 Skills (Standard Operating Procedures)")
    
    print("Discovering skills...")
    skills = discover_skills()
    print(f"Found {len(skills)} skills")
    
    if skills:
        for skill in skills:
            lines.append(f"- **{skill['name']}**: {skill['description']}")
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
