# Image Generation Capability Implementation Plan

Add detailed image creation and editing capabilities to the Universal Agent system using Gemini API, integrated as both an MCP tool (for universal access) and a specialized subagent (for complex creative workflows).

## User Review Required

> [!IMPORTANT]
> **Gradio Viewer**: The plan includes an optional Gradio-based image viewer. Should this auto-launch when images are generated, or remain a manual launch option?

> [!NOTE]
> **Cost Consideration**: Gemini image generation has per-request costs. ZAI Vision (free) will be used for image analysis/description. Gemini understanding will only be used when creative suggestions are needed.

---

## Orchestration Architecture

### Hub-and-Spoke Pattern (Subagent Coordination)

Subagents **cannot directly delegate to other subagents**. All coordination flows through the main agent:

```
User: "Create a report about AI with custom infographics"
                    ↓
            Main Agent (orchestrates)
                    ↓
    ┌─────────────────────────────────────────────┐
    │  Uses TodoWrite to track overall workflow:  │
    │  - [ ] Research AI topics                   │
    │  - [ ] Generate infographics (image-expert) │
    │  - [ ] Create report (report-creation-exp)  │
    │  - [ ] Deliver final work product           │
    └─────────────────────────────────────────────┘
                    ↓
    Option A: Main agent delegates to image-expert FIRST
              (gets image paths), then delegates to report-expert
              
    Option B: report-creation-expert calls generate_image tool
              directly (inherited tool access)
```

### Dynamic Composio Tool Discovery & Planning

All agents/subagents inherit access to the full Composio MCP server. This includes the critical **`COMPOSIO_SEARCH_TOOLS`** meta-tool for ad-hoc task planning.

#### `COMPOSIO_SEARCH_TOOLS` - The Planning Tool

When a subagent encounters an ad-hoc multi-step task (not decomposed from the original query), it should:

1. **Call `COMPOSIO_SEARCH_TOOLS`** with the use case description
2. **Get back**: 
   - `recommended_plan_steps` - Sequential steps to execute
   - `execution_guidance` - Pitfalls and best practices
   - `tool_slug` recommendations - Specific tools for each step
3. **Create TodoWrite checklist** from the recommended steps
4. **Execute sequentially** while tracking completion

Example `COMPOSIO_SEARCH_TOOLS` response:
```json
{
  "use_case": "generate infographic about renewable energy data",
  "recommended_plan_steps": [
    "Step 1: Gather data from trusted sources",
    "Step 2: Structure data for visualization", 
    "Step 3: Generate infographic with clear labels"
  ],
  "known_pitfalls": [
    "Avoid cluttered layouts - limit to 5-7 data points",
    "Ensure high contrast for accessibility"
  ],
  "tools": [
    {"tool_slug": "COMPOSIO_SEARCH_WEB", "purpose": "Find statistics"},
    {"tool_slug": "generate_image", "purpose": "Create visualization"}
  ]
}
```

> [!IMPORTANT]
> **COMPOSIO_SEARCH_TOOLS Scope Limitation**
> 
> This planner only knows about **remote Composio tools** (COMPOSIO_SEARCH_*, GMAIL_*, SLACK_*, etc.).
> It **does NOT know about**:
> - Local MCP tools (`mcp__local_toolkit__*`, `mcp__zai_vision__*`)
> - Skills and subagent capabilities
> - Custom tools in your prompt
> 
> **Use COMPOSIO_SEARCH_TOOLS for**:
> - Discovery of remote Composio capabilities you're unfamiliar with
> - External APIs (search, email, calendar, data sources)
> - Workflows involving multiple remote services
> 
> **DON'T use for local tools** - rely on your prompt guidance instead:
> - `generate_image`, `describe_image`, `preview_image` (local MCP)
> - `crawl_parallel`, `write_local_file` (local toolkit)
> - Skills, subagents, and other local capabilities
> 
> **Example**: Need stock market data? → Use `COMPOSIO_SEARCH_TOOLS` to find the right API.
>              Need to generate an image? → Use `generate_image` directly (documented in your prompt).

#### Which Agent Can Use What

| Agent | Can Dynamically Use |
|-------|---------------------|
| `image-expert` | `COMPOSIO_SEARCH_TOOLS` for planning, `COMPOSIO_SEARCH_*` for reference images |
| `report-creation-expert` | `generate_image` tool, `COMPOSIO_SEARCH_TOOLS` for report structure guidance |
| `slack-expert` | `generate_image` for visual posts, search tools for content |
| Main Agent | Any Composio connector, orchestrates subagent delegation |

Agents are **NOT limited to pre-planned tools**. They can use `COMPOSIO_SEARCH_TOOLS` to discover capabilities and get structured guidance for any emergent task.

### TodoWrite for Complex Workflows

All subagents should use `TodoWrite` (Claude SDK todo list) in conjunction with `COMPOSIO_SEARCH_TOOLS`:

1. **Call `COMPOSIO_SEARCH_TOOLS`** to get recommended steps
2. **Create TodoWrite checklist** from the response
3. **Track multi-step execution** with nested checkboxes
4. **Handle emergent sub-tasks** by adding nested todos dynamically

Example workflow (after COMPOSIO_SEARCH_TOOLS planning):
```
- [x] Called COMPOSIO_SEARCH_TOOLS for "generate infographic about AI trends"
- [x] Understand user request
- [ ] Execute planned steps
  - [ ] Step 1: Search for AI market statistics (COMPOSIO_SEARCH_WEB)
  - [ ] Step 2: Structure data points for visualization
  - [ ] Step 3: Generate infographic (generate_image tool)
  - [ ] Step 4: Review output and iterate if needed
- [ ] Embed generated images in HTML report
- [ ] Save to work_products/
```

---

## Proposed Changes

### Dependencies

#### [MODIFY] [pyproject.toml](file:///home/kjdragan/lrepos/universal_agent/pyproject.toml)
Add required packages:
```toml
dependencies = [
    # ... existing ...
    "google-genai>=1.0.0",  # Gemini API for image generation
    "gradio>=4.0.0",        # Optional: Image viewer UI
]
```

**Install command**: `uv add google-genai gradio`

---

### MCP Server - Core Tools

#### [MODIFY] [mcp_server.py](file:///home/kjdragan/lrepos/universal_agent/src/mcp_server.py)

Add two new tools after the `crawl_parallel` tool:

##### Tool 1: `generate_image`
```python
@mcp.tool()
def generate_image(
    prompt: str,
    input_image_path: str = None,
    output_dir: str = None,
    output_filename: str = None,
    preview: bool = False
) -> str:
    """
    Generate or edit an image using Gemini 2.5 Flash Image model.
    
    Args:
        prompt: Text description for generation, or edit instruction if input_image provided.
        input_image_path: Optional path to source image (for editing). If None, generates from scratch.
        output_dir: Directory to save output. Defaults to workspace work_products/media/.
        output_filename: Optional filename. If None, auto-generates with timestamp.
        preview: If True, launches Gradio viewer with the generated image.
        
    Returns:
        JSON with status, output_path, description, and viewer_url (if preview=True).
    """
    # Implementation uses google-genai SDK
    # - Text-only: generates new image
    # - Image + text: edits existing image
    # - Auto-generates filename with short description via describe_image
    # - Optionally launches Gradio viewer for human review
```

##### Tool 2: `describe_image`
```python
@mcp.tool()
def describe_image(image_path: str, max_words: int = 10) -> str:
    """
    Get a short description of an image using ZAI Vision (free).
    Useful for generating descriptive filenames.
    
    Args:
        image_path: Path to the image file.
        max_words: Maximum words in description (default 10).
        
    Returns:
        Short description suitable for filenames.
    """
    # Uses ZAI Vision MCP internally (already configured)
    # Falls back to timestamp-only naming if unavailable
```

##### Tool 3: `preview_image`
```python
@mcp.tool()
def preview_image(image_path: str, port: int = 7860) -> str:
    """
    Open an image in the Gradio viewer for human review.
    Useful for viewing any existing image in the workspace.
    
    Args:
        image_path: Absolute path to the image file.
        port: Port to launch Gradio on (default 7860).
        
    Returns:
        JSON with viewer_url (e.g., "http://127.0.0.1:7860").
    """
    # Launches scripts/gradio_viewer.py with the specified image
    # Non-blocking: runs in background, returns URL immediately
```

---

### Image Generation Skill

#### [NEW] [.claude/skills/image-generation/SKILL.md](file:///home/kjdragan/lrepos/universal_agent/.claude/skills/image-generation/SKILL.md)

```yaml
---
name: image-generation
description: "AI-powered image generation and editing using Gemini. Use when Claude needs to: 
  (1) Generate images from text descriptions, 
  (2) Edit existing images with instructions, 
  (3) Create infographics or charts, 
  (4) Generate visual assets for reports/presentations,
  (5) Work with .png, .jpg, .jpeg, .webp files for editing."
---

# Image Generation & Editing

## Overview
Generate and edit images using Gemini 2.5 Flash Image model via MCP tools.

## Quick Start

### Text-to-Image Generation
```python
result = generate_image(
    prompt="A modern infographic showing renewable energy statistics",
    output_dir="/path/to/work_products/media"
)
```

### Image Editing
```python
result = generate_image(
    prompt="Change the background to a sunset over mountains",
    input_image_path="/path/to/original.png",
    output_dir="/path/to/work_products/media"
)
```

## Best Practices
- Be specific in prompts: describe style, colors, composition
- For charts/infographics: include data points in the prompt
- For editing: describe what to change AND what to preserve
- Output is saved to work_products/media/ with descriptive filename

## Integration with Other Skills
- **Reports**: Call generate_image mid-report for custom graphics
- **PowerPoint**: Generate slide backgrounds or visual elements  
- **Documents**: Create embedded figures and diagrams
```

#### [NEW] [.claude/skills/image-generation/scripts/gemini_image.py](file:///home/kjdragan/lrepos/universal_agent/.claude/skills/image-generation/scripts/gemini_image.py)

Standalone script for image generation (also used by MCP tool internally):
- Handles both generation and editing workflows
- Saves images with descriptive filenames
- Integrates with ZAI Vision for auto-description

#### [NEW] [.claude/skills/image-generation/scripts/gradio_viewer.py](file:///home/kjdragan/lrepos/universal_agent/.claude/skills/image-generation/scripts/gradio_viewer.py)

Optional Gradio UI for interactive image editing:
- Drag-and-drop image upload
- Text prompt input
- Side-by-side before/after view
- Launches on `http://127.0.0.1:7860`

---

### Image Expert Subagent

#### [MODIFY] [main.py](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/main.py)

Add to `agents={}` dict (around line 1756):

```python
"image-expert": AgentDefinition(
    description=(
        "Expert for AI image generation and editing. "
        "DELEGATE when user requests: 'generate image', 'create image', 'edit image', "
        "'make a picture', 'design graphic', 'create infographic', 'visual for report', "
        "or wants to iteratively refine images through conversation."
    ),
    prompt=(
        f"Result Date: {datetime.now().strftime('%A, %B %d, %Y')}\n"
        f"CURRENT_SESSION_WORKSPACE: {workspace_dir}\n\n"
        "You are an **Image Generation Expert** using Gemini 2.5 Flash Image.\n\n"
        "## TASK MANAGEMENT (TodoWrite)\n"
        "Use TodoWrite to track complex workflows:\n"
        "```\n"
        "- [ ] Understand image request (style, content, purpose)\n"
        "- [ ] Generate initial image\n"
        "  - [ ] Craft detailed prompt\n"
        "  - [ ] Call generate_image tool\n"
        "- [ ] Review output with describe_image\n"
        "- [ ] Iterate if refinement needed\n"
        "- [ ] Confirm final output saved to work_products/media/\n"
        "```\n"
        "Mark items complete as you progress. Add nested todos for sub-steps.\n\n"
        "## AVAILABLE TOOLS\n"
        "**Primary Image Tools:**\n"
        "- `mcp__local_toolkit__generate_image` - Generate or edit images\n"
        "- `mcp__local_toolkit__describe_image` - Get image descriptions (free, via ZAI)\n"
        "- `mcp__zai_vision__analyze_image` - Detailed image analysis (free)\n\n"
        "**Dynamic Composio Access & Planning:**\n"
        "You inherit ALL Composio tools. For complex or unfamiliar tasks:\n"
        "- Call `COMPOSIO_SEARCH_TOOLS` ONLY for **remote Composio tools** (external APIs, data sources)\n"
        "- It does NOT know about local tools (generate_image, crawl_parallel, etc.)\n"
        "- Use the returned `recommended_plan_steps` to structure your TodoWrite list\n"
        "- Use `COMPOSIO_SEARCH_*` tools to find reference images, data, or material\n"
        "- Use workbench tools for code execution if needed\n\n"
        "**Example**: Need reference photos? Use COMPOSIO_SEARCH_TOOLS to find image search APIs.\n"
        "             Need to generate an image? Use generate_image (already in your tools).\n\n"
        "## WORKFLOW\n"
        "1. **Understand Request**: What style, content, purpose?\n"
        "2. **Generate/Edit**: Call generate_image with detailed prompt\n"
        "3. **Review**: Use describe_image or analyze_image to verify output\n"
        "4. **Iterate**: If user wants changes, edit the generated image\n"
        "5. **Save**: Images auto-save to work_products/media/\n\n"
        "## PROMPT CRAFTING TIPS\n"
        "- Be specific: 'modern, minimalist infographic with blue gradient'\n"
        "- Include style: 'photorealistic', 'illustration', 'line art'\n"
        "- For charts: describe the data and preferred visualization\n"
        "- For editing: describe what to change AND preserve\n\n"
        f"OUTPUT DIRECTORY: {workspace_dir}/work_products/media/"
    ),
    model="inherit",
),
```

#### [MODIFY] [agent_core.py](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/agent_core.py)

Add equivalent `image-expert` subagent definition (mirror main.py).

---

### Skill Trigger Configuration

#### [MODIFY] [main.py](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/main.py)

Add to `SKILL_PROMPT_TRIGGERS_OVERRIDE` (around line 884):

```python
SKILL_PROMPT_TRIGGERS_OVERRIDE = {
    "image-generation": [
        "image", "generate image", "create image", "edit image",
        "picture", "photo", "illustration", "graphic", "infographic",
        "visual", "design", ".png", ".jpg", ".jpeg", ".webp"
    ],
}
```

---

### Update Existing Skills/Agents

#### [MODIFY] [report-creation-expert prompt](file:///home/kjdragan/lrepos/universal_agent/src/universal_agent/main.py#L1770-L1820)

Add a new section to the report-creation-expert prompt for capability awareness:

```python
"## AVAILABLE VISUAL RESOURCES\n"
"You have direct access to image generation tools for custom graphics:\n\n"
"**Image Generation Tool:**\n"
"- `mcp__local_toolkit__generate_image(prompt, output_dir)` - Create infographics, charts, diagrams\n"
"- Images save to `{workspace}/work_products/media/` and return the file path\n"
"- You can call this tool directly - no need to delegate\n\n"
"**When to Use:**\n"
"- Reports with data that would benefit from visualization\n"
"- Executive summaries that need visual impact\n"
"- Complex topics that need explanatory diagrams\n\n"
"**How to Embed:**\n"
"1. Call `generate_image` with a descriptive prompt including data/content\n"
"2. Get back the saved image path (e.g., `work_products/media/ai_trends_chart_20251227.png`)\n"
"3. Embed in your HTML report:\n"
"   ```html\n"
"   <img src=\"file:///absolute/path/to/image.png\" alt=\"Description\" style=\"max-width: 100%;\">\n"
"   ```\n\n"
"**Example Prompts:**\n"
"- 'Infographic showing 3 key statistics: 45% growth, $2.3B market, 120 companies'\n"
"- 'Clean bar chart comparing renewable energy adoption: Solar 35%, Wind 28%, Hydro 22%'\n"
"- 'Modern diagram showing the flow from research to report to delivery'\n\n"
```

#### [MODIFY] [pptx SKILL.md](file:///home/kjdragan/lrepos/universal_agent/.claude/skills/pptx/SKILL.md)

Add a new section after "Design Principles":

```markdown
### AI-Generated Visual Elements

For slides that need custom graphics, illustrations, or unique images:

1. **Generate image**: `generate_image(prompt="description", output_dir="workspace/work_products/media")`
2. **Get path**: The tool returns the saved image path
3. **Embed in slide**: Reference the image path in your HTML slide or use PptxGenJS `addImage()`

Example prompts:
- "Modern flat icon of a cloud with data streams"
- "Gradient background transitioning from deep blue to purple"
- "Infographic showing 3 connected circles with arrows"
```

---

## Verification Plan

### Automated Tests

1. **Text-to-image generation**:
   ```bash
   cd /home/kjdragan/lrepos/universal_agent
   uv run python -c "
   from src.mcp_server import generate_image
   result = generate_image('A simple blue square with rounded corners', output_dir='/tmp')
   print(result)
   "
   ```

2. **Image editing**:
   ```bash
   # First generate a base image, then edit it
   uv run python -c "
   from src.mcp_server import generate_image
   # Generate base
   r1 = generate_image('A red circle on white background', output_dir='/tmp')
   # Edit it
   r2 = generate_image('Change the circle to blue', input_image_path=r1['output_path'], output_dir='/tmp')
   print(r2)
   "
   ```

3. **Image description**:
   ```bash
   uv run python -c "
   from src.mcp_server import describe_image
   desc = describe_image('/tmp/generated_image.png')
   print(desc)
   "
   ```

### Manual Verification

1. Run the agent and request: "Generate an image of a sunset over mountains"
2. Verify image saved to `work_products/media/`
3. Request: "Edit that image to add snow on the peaks"
4. Verify edited image saved with descriptive filename

### Integration Verification

1. Request: "Create a report about renewable energy with an infographic"
2. Verify `report-creation-expert` calls `generate_image` and embeds result
3. Request: "Create a PowerPoint about AI trends with custom graphics"
4. Verify slides include generated images

---

## Future Enhancements

### Slash Command Integration with `prompt_toolkit`

**Goal**: Add Discord/Slack-style slash commands with autocomplete to the CLI for quick access to workflows and tools.

**Library**: [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit)
- Autocomplete as you type with TAB
- Fuzzy matching (`/eval` → `/evaluate-run`)
- Metadata previews (show command description)
- Works alongside natural language input

**Example User Experience**:
```
Universal Agent> /view<TAB>
  /view-image          Open image in Gradio viewer
  /view-workspace      List workspace files

Universal Agent> /view-image work_products/media/sunset.png
[Opens Gradio viewer with sunset.png]

Universal Agent> show me the latest run analysis
[Agent interprets → executes /evaluate-run workflow]
```

**Integration Points**:
1. Scan `.agent/workflows/` for available workflows → auto-register as slash commands
2. Register tool shortcuts (e.g., `/view-image` → `preview_image` tool)
3. Maintain dual UX: slash commands for power users, natural language for everyone

**Implementation Approach**:
- Add `uv add prompt_toolkit`
- Wrap CLI input loop in `PromptSession` with `WordCompleter`
- If input starts with `/`, execute as shortcut; otherwise, pass to agent

**Priority**: Medium (nice-to-have, not blocking image generation)

---

## Related Documentation

- [035_AGENT_COLLEGE_ARCHITECTURE.md](file:///home/kjdragan/lrepos/universal_agent/Project_Documentation/035_AGENT_COLLEGE_ARCHITECTURE.md) - Subagent patterns
- [028_CLAUDE_SKILLS_INTEGRATION.md](file:///home/kjdragan/lrepos/universal_agent/Project_Documentation/028_CLAUDE_SKILLS_INTEGRATION.md) - Skill creation
- [pptx SKILL.md](file:///home/kjdragan/lrepos/universal_agent/.claude/skills/pptx/SKILL.md) - PowerPoint integration
