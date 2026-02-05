# Skills Infrastructure & Capability Gating

## Overview

The Universal Agent now implements a **Skills Infrastructure** inspired by Clawdbot. This allows the agent to dynamically load specialized workflows ("Skills") based on the available system environment, preventing hallucinations and errors caused by missing dependencies.

## Key Features

### 1. Progressive Disclosure Architecture
Skills are stored in `.claude/skills/`. To save context window space, the agent uses a "Level 1" loading strategy:
-   **Startup**: Only the `name` and `description` from `SKILL.md` are loaded into the system prompt.
-   **Execution**: When the model decides to use a skill, it reads the full `SKILL.md` file.
-   **Deep Dive**: If the skill requires complex schemas or templates, the model is instructed to read files from the skill's `references/` directory on demand.

### 2. Dependency Gating
The agent automatically hides skills if the host system lacks the required tools. This is controlled via `SKILL.md` YAML frontmatter.

**Example `SKILL.md`:**
```yaml
---
name: github
description: Interact with GitHub issues and PRs.
metadata:
  clawdbot:
    requires:
      bins: ["gh"]
---
```

**Logic (`prompt_assets.py`):**
-   The system checks for `gh` using `shutil.which("gh")`.
-   If `gh` is **missing**, the `github` skill is **removed** from the system prompt. The model effectively doesn't know it exists.
-   If `gh` is **present**, the skill is listed in `<available_skills>`.

## Available Skills

The following skills have been migrated to Universal Agent. They will only appear if their dependencies are met:

| Skill | Description | Dependency |
| :--- | :--- | :--- |
| **github** | Manage Issues, PRs, and Runs | `gh` CLI |
| **slack** | Send messages, react, manage channels | `curl` (API) |
| **discord** | Send messages via Webhook | `curl` |
| **tmux** | Manage long-running background sessions | `tmux` |
| **trello** | Manage boards and cards | `curl` |
| **notion** | Manage pages and databases | `curl` |
| **summarize** | Summarize long text files | Python (builtin) |
| **coding-agent** | Specialized sub-agent for code tasks | Bash |

## How to Add New Skills

1.  Create a directory in `.claude/skills/<skill-name>`.
2.  Add a `SKILL.md` file.
3.  Add the **Gating Metadata** to the frontmatter:

```yaml
---
name: my-new-skill
description: Does amazing things with the 'foo' binary.
metadata:
  clawdbot:
    requires:
      bins: ["foo"]   # <--- The system will check for this!
      anyBins: ["bar", "baz"] # <--- OR verify one of these exists
---

# My New Skill
Instructions go here...
```
4.  Restart the agent. The skill will auto-register if `foo` is in your `$PATH`.
