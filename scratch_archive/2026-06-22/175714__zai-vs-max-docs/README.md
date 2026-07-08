# ZAI vs Max — script-inference auth & GLM-skills findings (2026-06-22)

The three canonical docs updated by PR #1162. The **new** content in each:

- **[06_platform/05_environments.md](06_platform/05_environments.md)** → new section **"Running inference from a script (outside an interactive session)"** (raw SDK ≠ subscription; CLI/Agent-SDK ride Max; headless OAuth token; subprocess inherits the launch alias's env) + the skill-creator worked error-case.
- **[07_tools/03_skills_system.md](07_tools/03_skills_system.md)** → new **Gotcha** (last bullet): GLM *does* invoke real skills via the Agent SDK; the command-file/`claude -p` test harness is what false-reads 0 — corrects "GLM is skill-blind".
- **[01_architecture/04_model_choice_and_resolution.md](01_architecture/04_model_choice_and_resolution.md)** → new note under §1: a bare `--model glm-5.2` is **honored, not** tier-downgraded; tier vs wire-id.
