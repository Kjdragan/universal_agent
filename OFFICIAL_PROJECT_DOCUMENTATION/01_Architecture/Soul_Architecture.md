# Soul Architecture

The "Soul" is the definitive source of truth for the Universal Agent's identity, personality, and core operational directives. It decouples the agent's persona from the underlying logic.

## 1. Overview

The Soul Architecture ensures that the agent's behavior (e.g., being a Senior Staff Engineer named Simon) is maintained consistently across different interfaces (CLI, Web, Telegram). This prevents "identity drift" and allows for rapid persona customization.

## 2. The Default Persona: "Simon"

The system defaults to a persona named **Simon**, designed to be:

- **Role**: Senior Staff Engineer.
- **Traits**: Opinionated, autonomous, high-velocity, and proactive.
- **Philosophy**: "Solve the problem, don't just answer the question."

## 3. Storage and Hierarchy

Personas are defined in Markdown files. The agent uses a tiered injection system to load the Soul, where the first file found wins:

1. **Session Override**: `<Active Workspace>/SOUL.md`  
    *Allows for task-specific personas (e.g., "Roleplay as a Technical Writer").*
2. **Global Standard**: `src/universal_agent/prompt_assets/SOUL.md`  
    *The default system identity.*
3. **Legacy Fallback**: `RepoRoot/SOUL.md`

## 4. Implementation Logic

The loading and injection happen in `src/universal_agent/prompt_assets/`:

- **Injection**: The `AgentSetup` class reads the Soul file and injects its content at the absolute top of the system prompt before any tool descriptions.
- **Consistency**: In-process hooks and the `main.py` entry point ensure that even simple queries embody the persona.

## 5. Customizing for Tasks

To change the agent's behavior for a specific simulation or project:

1. Create a `SOUL.md` in the workspace directory.
2. Define the traits and constraints.
3. The agent will detect the override and log: `Loaded Soul override from workspace`.
