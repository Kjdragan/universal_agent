# Agent Skills Directory (`.agents/skills/`)

Last updated: 2026-04-09

## Purpose

The `.agents/skills/` directory contains reusable agent skills that are separate from the primary `.claude/skills/` directory. These skills are designed for portability and can be loaded by different agent runtimes.

## Directory Structure

```
.agents/
├── rules/
│   └── project-variables-and-secrets.md
└── skills/
    ├── agent-browser/
    │   └── SKILL.md          # Browser automation via Vercel headless browser
    ├── agentmail/
    │   ├── SKILL.md          # AgentMail integration for Simone's email
    │   └── references/
    │       ├── websockets.md # WebSocket-based real-time notifications
    │       └── webhooks.md   # HTTP webhook delivery
    ├── arxiv-specialist/
    │   └── SKILL.md          # arXiv paper search and analysis
    ├── banana-squad/
    │   └── SKILL.md          # Infographic prompt variation generator
    ├── captcha-solver/
    │   ├── SKILL.md          # CAPTCHA bypass via NopeCHA extension
    │   └── scripts/
    │       └── solve_with_nopecha.py
    ├── clean-code/
    │   └── SKILL.md          # Clean Code principles from Robert C. Martin
    ├── gemini/
    │   └── SKILL.md          # Gemini CLI for Q&A and generation
    ├── gmail/
    │   └── SKILL.md          # Gmail via gws CLI for Kevin's email
    ├── github/
    │   └── SKILL.md          # GitHub CLI integration
    ├── image-generation/
    │   └── SKILL.md          # AI-powered image generation via Gemini
    ├── residential-proxy/
    │   ├── SKILL.md          # One-off rotating residential proxy via Webshare
    │   └── scripts/
    │       ├── get_proxy_url.py
    │       └── proxy_fetch.py
    ├── skill-judge/
    │   └── SKILL.md          # Evaluate skill quality against specifications
    ├── systematic-debugging/
    │   ├── SKILL.md          # Systematic debugging methodology
    │   ├── root-cause-tracing.md
    │   ├── defense-in-depth.md
    │   └── condition-based-waiting.md
    ├── youtube-media/
    │   ├── SKILL.md          # Native YouTube audio/video download via PoT bypass
    │   └── scripts/
    │       └── fetch_youtube_media.py
    └── vp-orchestration/
        ├── SKILL.md          # External VP agent mission control
        └── references/
            └── tool_reference.md
    └── ... (50+ additional skills)
```

## Available Skills

The directory contains 50+ skills spanning categories:

| Category | Example Skills |
|----------|---------------|
| **Communication** | `agentmail`, `gmail`, `discord`, `slack`, `telegram` |
| **Development** | `clean-code`, `systematic-debugging`, `github`, `git-commit` |
| **Research** | `arxiv-specialist`, `gemini-url-context-scraper`, `reddit-intel` |
| **Creativity** | `banana-squad`, `image-generation`, `video-remotion`, `youtube-media` |
| **Integration** | `vp-orchestration`, `google_calendar`, `notion`, `trello` |
| **Data** | `data-fusion`, `financial-extractor`, `pdf` |
| **Obsidian** | `obsidian-cli`, `obsidian-bases`, `obsidian-markdown`, `obsidian-power-user` |
| **Anti-Bot Bypass** | `residential-proxy`, `captcha-solver` |

### Featured Skills

| Skill | Purpose |
|-------|---------|
| `clean-code` | Applies principles from Robert C. Martin's "Clean Code" - naming, functions, comments, error handling, class design |
| `agentmail` | Simone's native email inbox via AgentMail for sending/receiving emails independently from Kevin's Gmail |
| `gmail` | Gmail via gws CLI for acting as Kevin when sending emails from his Gmail account |
| `skill-judge` | Evaluate agent skill quality against official specifications and best practices |
| `systematic-debugging` | Systematic debugging methodology - always find root cause before proposing fixes |
| `vp-orchestration` | Operate external primary VP agents through tool-first mission control |
| `image-generation` | AI-powered image generation and editing using Gemini |
| `residential-proxy` | One-off rotating residential proxy via Webshare — bypass datacenter IP blocks on target sites |
| `captcha-solver` | Automated CAPTCHA bypass using NopeCHA browser extension — supports Cloudflare Turnstile, reCAPTCHA, hCaptcha |
| `youtube-media` | Native YouTube audio/video binary download via PoT (Proof-of-Origin) proxy bypass — avoids residential proxy billing for large media payloads |

## Usage

These skills follow the standard SKILL.md format with YAML frontmatter:

```yaml
---
name: skill-name
description: "When and how to use this skill"
---
```

To discover all available skills, browse the `.agents/skills/` directory or use:

```bash
ls -1 .agents/skills/
```

## Related Documentation

- Primary skills directory: `.claude/skills/`
- Glossary definition: `docs/Glossary.md` (see "Skill" entry)
- SDK Permissions, Hooks & Subagents: `docs/002_SDK_PERMISSIONS_HOOKS_SUBAGENTS.md`
- Email Architecture: `docs/03_Operations/82_Email_Architecture_And_AgentMail_Source_Of_Truth_2026-03-06.md`
- Residential Proxy Policy: `docs/03_Operations/86_Residential_Proxy_Architecture_And_Usage_Policy_Source_Of_Truth_2026-03-06.md`
