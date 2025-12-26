# PDF Skill Strategy & Smart Routing Architecture

## Overview
This document outlines the architecture for the "PDF Skill," which provides the agent with the capability to generate high-quality PDF documents from various sources (Markdown, HTML).

**Key Design Principle**: A "Smart Routing" strategy that decouples content generation from format conversion while preventing compatibility errors.

## 1. Single Source of Truth
The logic for PDF generation is **exclusively** defined in the knowledge base, specifically:
`/.claude/skills/pdf/SKILL.md`

*   **No Code Duplication**: The Python codebase (`src/universal_agent/main.py`) does *not* contain functional code for PDF generation. It only contains keyword triggers (`"pdf": ["reportlab", ...]`).
*   **Dynamic Loading**: The agent reads `SKILL.md` at runtime. Updates to the markdown file instantly propagate to agent behavior.

## 2. Smart Routing Strategy
To handle the ambiguity of "Create a PDF," we implement conditional routing based on the *intermediate* file format chosen by the Report Expert.

| Intermediate Format | Required Tool | Why? |
|---------------------|---------------|------|
| **HTML (`.html`)** | `google-chrome --headless` | Preserves CSS, grids, colors, and "dashboard" layouts. Pandoc strips this. |
| **Markdown (`.md`)** | `pandoc` | Preserves formal typography, headers, and academic structure. Chrome renders raw source. |

### The Logic (in `SKILL.md`)
```markdown
### Scenario A: Source is HTML
**Use Google Chrome (Headless)**.
Command: `google-chrome --headless --print-to-pdf=output.pdf ...`

### Scenario B: Source is Markdown
**Use Pandoc**.
Command: `pandoc input.md -o output.pdf --pdf-engine=weasyprint`
```

## 3. Dependency Management (The "Perfect Path")
We explicitly favor a Python-native, portable "Perfect Path" over system-heavy defaults.

*   **Pandoc Engine**: `weasyprint` (Python) instead of `pdflatex` (System/apt).
    *   **Why**: WeasyPrint is pip-installable, lighter (no 4GB TeX install), and supports CSS styling for markdown.
*   **Installation**: Managed via `uv`.
    *   `uv add weasyprint` ensures it is available in the `.venv`.
*   **System Binary**: `pandoc` (via `apt-get`) is the *only* required system binary (besides Chrome).

## 4. Environment Resilience
The agent is trained (via `SKILL.md`) to check for tool availability:
1.  **Check**: `which google-chrome` / `which pandoc`
2.  **Fallback**: Use the available tool if the preferred one is missing (though format quality may suffer).

## 5. Verification History
*   **Dec 26, 2025**:
    *   **Registry Check**: Confirmed no hardcoded python logic.
    *   **Regression Test**: Verified agent respected the `SKILL.md` routing (HTML -> Chrome) successfully.
    *   **Error Prevention**: Verified `GMAIL_SEND_EMAIL` schema guidance in `composio.md` prevents delivery failures.
