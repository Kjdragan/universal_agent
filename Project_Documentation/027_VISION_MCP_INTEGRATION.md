# Vision MCP Server Integration

**Date:** December 25, 2025
**Status:** ACTIVE
**Related Files:**
- `src/universal_agent/main.py:1318-1327` - MCP server configuration
- `.env` - Environment variables (Z_AI_API_KEY, Z_AI_MODE)

---

## Overview

The **Z.AI Vision MCP Server** provides GLM-4.6V multimodal capabilities for image and video analysis. It runs as an external MCP server via `npx` and integrates with the Universal Agent's existing MCP architecture.

---

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `Z_AI_API_KEY` | Z.AI API key from [z.ai/manage-apikey](https://z.ai/manage-apikey/apikey-list) | Required |
| `Z_AI_MODE` | Service platform selection | `ZAI` |

### MCP Server Config (main.py)

```python
"zai_vision": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@z_ai/mcp-server"],
    "env": {
        "Z_AI_API_KEY": os.environ.get("Z_AI_API_KEY", ""),
        "Z_AI_MODE": os.environ.get("Z_AI_MODE", "ZAI"),
    },
},
```

---

## Tool Catalog

All tools are invoked with prefix: `mcp__zai_vision__<tool_name>`

| Tool | Purpose | Use Case |
|------|---------|----------|
| `ui_to_artifact` | Convert UI screenshots to code/specs | Generate HTML/CSS from mockups |
| `extract_text_from_screenshot` | OCR for code, terminals, docs | Extract code from screenshots |
| `diagnose_error_screenshot` | Analyze error snapshots | Debug from error screenshots |
| `understand_technical_diagram` | Interpret architecture/UML/ER diagrams | Document system architecture |
| `analyze_data_visualization` | Read charts and dashboards | Extract insights from graphs |
| `ui_diff_check` | Compare two UI screenshots | Detect visual regressions |
| `image_analysis` | General-purpose image understanding | Fallback for other image tasks |
| `video_analysis` | Video understanding (≤8MB MP4/MOV/M4V) | Describe video content |

---

## Usage Examples

### Image Analysis
```
User: "What does screenshot.png show?"
Agent: mcp__zai_vision__image_analysis(image_path="screenshot.png")
```

### OCR
```
User: "Extract the code from code_screenshot.png"
Agent: mcp__zai_vision__extract_text_from_screenshot(image_path="code_screenshot.png")
```

### UI to Code
```
User: "Convert this UI mockup to HTML"
Agent: mcp__zai_vision__ui_to_artifact(image_path="mockup.png", output_type="html")
```

---

## Best Practices

1. **Image Path**: Provide absolute path or path relative to session workspace
2. **Image Size**: For video_analysis, keep files ≤8MB
3. **Supported Formats**: PNG, JPG, JPEG for images; MP4, MOV, M4V for video

---

## Quota Limits

| Plan | Quota |
|------|-------|
| Lite | 100 calls + 5hr prompt pool |
| Pro | 1000 calls + 5hr prompt pool |
| Max | 4000 calls + 5hr prompt pool |

---

## References

- [NPM Package](https://www.npmjs.com/package/@z_ai/mcp-server)
- [Z.AI API Keys](https://z.ai/manage-apikey/apikey-list)
- [GLM-4.6V Vision Model](https://docs.z.ai/guides/vlm/glm-4.6v)

---

*Document Version: 1.0*
*Last Updated: December 25, 2025*
