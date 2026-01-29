# Prompt Assets

This directory contains the core "personality" and "instructional" assets for the Universal Agent.

## Files

*   **`SOUL.md`**: The Persona Definition. Defines *who* the agent is (e.g., "Simon, the Senior Staff Engineer"). This content is injected at the **very top** of the System Prompt, overriding default behaviors.

## Usage

These assets are automatically loaded by:
1.  `src/universal_agent/main.py` (CLI Entry Point)
2.  `src/universal_agent/agent_setup.py` (Library Entry Point)

## Customization

To temporarily override the persona for a specific session:
1.  Create a `SOUL.md` file in your active workspace directory (e.g., `./workspace/SOUL.md`).
2.  The system will prefer the workspace version over this global version.
