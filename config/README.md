# Config Directory

This directory holds machine-local configuration files that should not be committed to git.

## User Profile

`config/user_profile.json` is intended to store private user profile information (including potentially sensitive PII like address) that the agent can use to:
- Choose correct defaults (timezone, home city) for tools like Google Maps.
- Personalize responses across sessions.

Notes:
- The repo `.gitignore` excludes `config/user_profile.json` and `config/user_profile.md`.
- Keep access controls in mind: anything in this file may be injected into the system prompt at runtime.

