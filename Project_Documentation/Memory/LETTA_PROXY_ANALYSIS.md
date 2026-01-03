# Letta Claude Code Proxy - Integration Analysis
## Can We Use Letta Memory with Our Universal Agent?

**Document Version**: 1.0
**Last Updated**: 2025-12-29
**Status**: Analysis Complete

---

## Executive Summary

Letta's Claude Code proxy is an **interesting but limited** approach that works for Claude Code CLI but **cannot be directly applied** to our Universal Agent (which uses the Claude Agent SDK Python library).

**Key Finding:** The Letta proxy works by intercepting Anthropic API calls at the HTTP level. Our Claude Agent SDK uses a **subprocess-based architecture** that talks to the Claude Code CLI binary, not direct HTTP calls to Anthropic.

**Verdict:** We cannot use Letta's proxy as-is. We would need to either:
1. Build a custom Letta integration for the Claude Agent SDK
2. Continue developing our own memory system (which is already quite capable)

---

## How Letta's Claude Code Proxy Works

### Architecture

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   Claude Code   │      │   Letta Proxy   │      │   Anthropic     │
│   (CLI/Editor)  │─────▶│   (API Gateway)  │─────▶│   API           │
└─────────────────┘      └─────────────────┘      └─────────────────┘
                                │
                                ▼
                        ┌─────────────────┐
                        │   Letta Agent   │
                        │   (Memory DB)   │
                        └─────────────────┘
```

### The Trick: Base URL Redirection

Letta's proxy works by having Claude Code redirect its API calls:

```bash
# Normal Claude Code (direct to Anthropic)
export ANTHROPIC_API_KEY=sk-ant-...
claude

# With Letta (redirects through Letta)
export ANTHROPIC_BASE_URL=https://api.letta.com/v1/anthropic
export ANTHROPIC_AUTH_TOKEN=sk-let-...
claude
```

### Flow

1. **Claude Code makes request** → Sends to `https://api.letta.com/v1/anthropic` (instead of Anthropic)
2. **Letta intercepts** → Prepends agent's memory blocks to the system prompt
3. **Letta forwards to Anthropic** → Processes the augmented request
4. **Response returns** → Letta passes response back to Claude Code
5. **Background processing** → Letta's "sleeptime agent" analyzes conversation and updates memory

---

## Our Universal Agent Architecture

### How We Use Claude Agent SDK

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│  Universal      │      │  Claude Agent   │      │  Claude Code    │
│  Agent          │─────▶│  SDK (Python)   │─────▶│  CLI Binary     │
│  (main.py)      │      │                 │      │  (subprocess)   │
└─────────────────┘      └─────────────────┘      └─────────────────┘
                                                              │
                                                              ▼
                                                      ┌─────────────────┐
                                                      │   Anthropic     │
                                                      │   API           │
                                                      └─────────────────┘
```

### Key Architecture Points

**From `claude_agent_sdk/client.py`:**
```python
class ClaudeSDKClient:
    def __init__(self, options, transport):
        # Uses SubprocessCLITransport to spawn claude CLI
        ...

class SubprocessCLITransport(Transport):
    def __init__(self, prompt, options):
        # Finds Claude Code CLI binary
        self._cli_path = options.cli_path or self._find_cli()

    def connect(self):
        # Spawns subprocess: subprocess.Popen(["claude", ...])
        ...
```

**Critical Point:** The Claude Agent SDK does NOT make HTTP calls to Anthropic directly. It:
1. Spawns the Claude Code CLI as a subprocess
2. Communicates via stdin/stdout using JSON protocol
3. The CLI handles all Anthropic API communication

---

## Why Letta Proxy Won't Work

### Problem 1: No Base URL Configuration

The Claude Agent SDK `SubprocessCLITransport` looks for the Claude Code CLI binary:
```bash
# From subprocess_cli.py:70-100
def _find_cli(self) -> str:
    # First, check for bundled CLI
    # Fall back to system-wide search
    if cli := shutil.which("claude"):
        return cli
```

It then spawns the CLI directly:
```python
subprocess.Popen([cli_path, ...])
```

There is **no parameter** to set a custom base URL or API endpoint. The SDK assumes the CLI will handle its own API configuration.

### Problem 2: Different Communication Protocol

- **Letta Proxy:** Expects HTTP requests with Anthropic API format
- **Claude Agent SDK:** Uses JSON-over-stdin/stdout protocol with the CLI

### Problem 3: CLI Binary Makes API Calls

The actual HTTP requests to Anthropic are made by the **Claude Code CLI binary**, not by the Python SDK. The Letta proxy would need to be in the CLI's request path, not the SDK's.

---

## Alternative: Custom Letta Integration

Since we can't use the existing proxy, here are alternatives:

### Option 1: Letta Python SDK + Direct Anthropic API

**Approach:** Bypass Claude Agent SDK entirely, use Letta's Python SDK directly with Anthropic's API.

```python
from letta_client import Letta
import anthropic

# Letta client for memory
letta = Letta(api_key=os.getenv("LETTA_API_KEY"))
agent = letta.agents.create(...)

# Anthropic client for inference
anthropic = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Manual loop
def process_turn(user_message):
    # Get memory from Letta
    agent_state = letta.agents.messages.create(agent_id, ...)
    memory_context = format_memory(agent_state.memory_blocks)

    # Call Anthropic with memory
    response = anthropic.messages.create(
        model="claude-3-5-sonnet-20241022",
        system=system_prompt + memory_context,
        messages=[...]
    )

    # Update Letta memory (background)
    letta.agents.passages.insert(...)

    return response
```

**Pros:**
- Full Letta memory capabilities
- Direct control

**Cons:**
- Lose Claude Agent SDK features (tool use, MCP, etc.)
- Need to reimplement tool calling logic
- Significant rewrite of our agent

### Option 2: Wrap Our Memory System with Letta-Compatible API

**Approach:** Keep our memory system, but enhance it to be Letta-compatible.

```python
# Our existing system, but with Letta-style enhancements
from Memory_System.manager import MemoryManager

class LettaStyleMemoryManager(MemoryManager):
    """Our memory system with Letta-compatible features."""

    def get_letta_agent_state(self, agent_id: str):
        """Return memory in Letta format."""
        blocks = self.get_agent_blocks(agent_id)
        return {
            "memory_blocks": [
                {
                    "label": b.label,
                    "value": b.value,
                    "limit": b.limit,
                    "description": b.description
                }
                for b in blocks
            ],
            "archival_memory": self.storage.list_archival(agent_id)
        }
```

**Pros:**
- Keep our existing architecture
- Add Letta features incrementally (per our PRD)
- No external dependency

**Cons:**
- Need to implement the features ourselves

### Option 3: Hybrid Approach (Recommended)

**Approach:** Use Letta for "cloud" features, keep our system for local.

```python
class HybridMemoryManager:
    def __init__(self):
        self.local = MemoryManager()  # Our system
        self.letta = None  # Optional Letta client

    def save_memory(self, label, value, use_letta=False):
        if use_letta and self.letta:
            self.letta.agents.blocks.create(...)
        else:
            self.local.update_memory_block(label, value)
```

**Pros:**
- Best of both worlds
- Gradual migration possible
- Local-first with optional cloud

**Cons:**
- More complex architecture

---

## Letta Proxy Technical Details

### What the Proxy Actually Does

Based on the documentation, the Letta proxy:

1. **Intercepts HTTP requests** at `https://api.letta.com/v1/anthropic`
2. **Extracts messages** from request body
3. **Looks up agent** by `X-LETTA-AGENT-ID` header or uses default
4. **Retrieves memory blocks** from Letta database
5. **Prepends memory** to system prompt
6. **Forwards request** to actual Anthropic API
7. **Returns response** to client
8. **Background task** analyzes conversation for memory formation

### "Sleeptime Agent"

Letta mentions a "sleeptime agent" that processes conversations in the background:

> "A Letta sleeptime agent processes the conversation and performs memory operations in the background"

This is likely a separate agent call that:
- Reviews the conversation transcript
- Decides what to remember
- Calls `memory_replace`, `archival_memory_insert`, etc.

**Note:** This is essentially the same as our "Active Memory Management" approach—just run as a background process instead of inline.

---

## Comparison: Letta vs Our Memory System

| Feature | Letta Proxy | Our System |
|---------|-------------|------------|
| **Core Memory** | ✅ Blocks with limits | ✅ Blocks (no limits yet) |
| **Archival Memory** | ✅ Semantic search | ✅ ChromaDB semantic search |
| **Active Memory Formation** | ✅ Sleeptime agent | ⚠️ Manual (PRD in progress) |
| **Multi-Agent** | ✅ Shared blocks | ⚠️ Planned |
| **Character Limits** | ✅ Yes | ⚠️ Planned |
| **Persistence** | ✅ Cloud database | ✅ Local SQLite/ChromaDB |
| **Privacy** | ⚠️ Cloud-hosted | ✅ Local-first |
| **Cost** | 💰 Letta credits | 💰 Free (local) |
| **Integration** | ⚠️ Proxy only | ✅ Native to our agent |

---

## Recommendations

### Short Term: Continue Our Memory Development

**Rationale:**
1. Our system is already ~60% of Letta's functionality
2. Local-first privacy is valuable
3. No external dependencies or costs
4. We can add Letta's features incrementally (per PRD)

**Next Steps:**
1. Implement Active Memory Management PRD (~4 hours)
2. Add Character Limits (Phase 1 of main PRD)
3. Add Shared Memory (Phase 2 of main PRD)

### Medium Term: Letta Compatibility Layer

**Rationale:**
- Could offer Letta export/import
- Allow migration between systems
- Support Letta `.af` (Agent File) format

**Implementation:**
```python
def export_to_letta_format(self, agent_id: str) -> dict:
    """Export our memory to Letta-compatible format."""
    blocks = self.get_agent_blocks(agent_id)
    archival = self.storage.list_archival(agent_id)

    return {
        "memory_blocks": [
            {
                "label": b.label,
                "value": b.value,
                "limit": getattr(b, 'limit', 5000),
                "description": b.description
            }
            for b in blocks
        ],
        "archival_memory": [
            {
                "content": a.content,
                "tags": a.tags,
                "timestamp": a.timestamp.isoformat()
            }
            for a in archival
        ]
    }
```

### Long Term: Evaluate Direct Letta Integration

**Rationale:**
- If we need cloud sync across devices
- If Letta adds features we can't replicate
- If users want Letta compatibility

**Approach:**
- Use Letta Python SDK for memory only
- Keep Claude Agent SDK for agent execution
- Hybrid architecture

---

## Open Questions

1. **Does Letta offer a Python SDK for direct integration?**
   - Documentation mentions `letta-client` package
   - May be worth exploring for hybrid approach

2. **Can Claude Code CLI be configured with a custom base URL?**
   - If yes, we might be able to use Letta proxy after all
   - Need to check CLI documentation/source

3. **What is Letta's pricing for long-term use?**
   - Free tier: $5/month credits
   - Beyond that: unknown
   - Our local system has no ongoing cost

4. **Does Letta offer self-hosting?**
   - Documentation mentions "Letta Cloud" and "self-hosted deployments"
   - Self-hosting could address privacy concerns

---

## Conclusion

Letta's Claude Code proxy is an elegant solution **for Claude Code specifically**, but it cannot be directly applied to our Universal Agent due to architectural differences:

- **Letta Proxy:** Intercepts HTTP calls from Claude Code CLI
- **Our Agent:** Uses Claude Agent SDK which spawns CLI as subprocess

**Our path forward:**
1. ✅ Continue developing our memory system (per existing PRDs)
2. ✅ Add Active Memory Management (quick win, ~4 hours)
3. ✅ Implement Letta-compatible features incrementally
4. ⚠️ Evaluate Letta Python SDK for hybrid approach (if needed)

**The good news:** Our memory system architecture is solid and already has most of Letta's core capabilities. With the enhancements outlined in our PRDs, we can achieve Letta-level functionality while maintaining local-first privacy and control.

---

**Document Status:** Analysis Complete
**Related Documents:**
- [ROADMAP_PRD.md](ROADMAP_PRD.md) - Main memory enhancement roadmap
- [ACTIVE_MEMORY_MANAGEMENT_PRD.md](ACTIVE_MEMORY_MANAGEMENT_PRD.md) - Quick win for autonomous memory
