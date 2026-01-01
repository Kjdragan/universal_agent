# Claude Skills Ecosystem Research

**Date:** December 25, 2025
**Status:** ACTIVE
**Raw Data:** [claude_skills_raw_20251225_164836.json](/home/kjdragan/lrepos/universal_agent/SAVED_REPORTS/claude_skills_raw_20251225_164836.json)

---

## Executive Summary

Agent-driven raw research discovered **2,300+ Claude skills repositories** with **39 curated awesome-lists** and **8,400+ files** in `.claude/skills/` paths across GitHub. This document catalogs the most relevant for Universal Agent enhancement.

---

## Search Statistics

| Search Type | Query | Results |
|-------------|-------|---------|
| Awesome Lists | `awesome-claude-skills` | 39 repos |
| Skills Repos | `claude-skills` | 2,218 repos |
| Skills Path Search | `path:.claude/skills` | 8,464 files |

---

## Top Priority Repositories

### 🔥 Tier 1: High Value (Immediate Investigation)

| Repository | Stars | Description | Why Relevant |
|-----------|-------|-------------|--------------|
| [wshobson/agents](https://github.com/wshobson/agents) | 23K | Multi-agent orchestration for Claude Code | Orchestration patterns for our sub-agents |
| [obra/superpowers](https://github.com/obra/superpowers) | 11K | Core skills library (TDD, debugging) | Battle-tested skills we can adopt |
| [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem) | 9K | AI memory plugin, context injection | Long-term memory for sessions |
| [diet103/claude-code-infrastructure-showcase](https://github.com/diet103/claude-code-infrastructure-showcase) | 7.8K | Skill auto-activation, hooks, agents | Reference implementation |
| [yusufkaraaslan/Skill_Seekers](https://github.com/yusufkaraaslan/Skill_Seekers) | 5.5K | Convert docs/GitHub/PDFs to skills | Auto-generate skills from our docs |

### 📊 Tier 2: Worth Exploring

| Repository | Stars | Description |
|-----------|-------|-------------|
| [K-Dense-AI/claude-scientific-skills](https://github.com/K-Dense-AI/claude-scientific-skills) | 2.3K | Scientific computing, genomics, data analysis |
| [ComposioHQ/awesome-claude-skills](https://github.com/ComposioHQ/awesome-claude-skills) | 10.7K | Curated list with Python integration focus |
| [travisvn/awesome-claude-skills](https://github.com/travisvn/awesome-claude-skills) | 3.7K | Comprehensive awesome-list |
| [SawyerHood/dev-browser](https://github.com/SawyerHood/dev-browser) | 847 | Browser skill with Puppeteer |
| [oh-my-opencode](https://github.com/code-yeongyu/oh-my-opencode) | 3K | OpenCode compatible, multi-LLM support |

### 🔐 Specialized Domains

| Repository | Focus |
|-----------|-------|
| [davydany/awesome-claude-skills-for-cybersecurity](https://github.com/davydany/awesome-claude-skills-for-cybersecurity) | Security testing |
| [athola/claude-night-market](https://github.com/athola/claude-night-market) | Git workflow, bug review, TDD |
| [Eyadkelleh/awesome-claude-skills-security](https://github.com/Eyadkelleh/awesome-claude-skills-security) | Pentesting, SecLists |
| [iSerter/laravel-claude-agents](https://github.com/iSerter/laravel-claude-agents) | Laravel sub-agents |

---

## Key Insights

### 1. Memory Problem Solved?
**claude-mem** (9K⭐) captures everything Claude does and injects relevant context back. Could address our "agent forgets context across sessions" challenge.

### 2. Auto-Skill Generation
**Skill_Seekers** (5.5K⭐) converts documentation, GitHub repos, and PDFs into skills automatically. We could use this to generate skills from our own project documentation.

### 3. Mature Skills Library
**obra/superpowers** (11K⭐) is the most mature - includes TDD patterns, debugging workflows, and `/brainstorm`, `/write-plan`, `/execute-plan` commands.

### 4. Few Actual Implementations
The `path:.claude/skills` search found only 68 repos with actual skill directories - most "skills repos" are curated lists, not implementations.

---

## Action Items

- [ ] **Immediate**: Deep-dive `claude-mem` for memory injection patterns
- [ ] **Immediate**: Evaluate `Skill_Seekers` for auto-generating skills from docs
- [ ] **Short-term**: Test installing `obra/superpowers` library
- [ ] **Research**: Compare `wshobson/agents` orchestration with our approach
- [ ] **Consider**: `claude-scientific-skills` for research workflows

---

## Methodology Note

This research was conducted using **raw research mode** - the agent used search tools without delegating to report-creation-expert or using crawl_parallel. This proves the agent can perform lightweight discovery tasks when explicitly instructed.

---

*Document Version: 1.0*
*Last Updated: December 25, 2025*
