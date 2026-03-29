# Model Choice, Resolution, and Z.AI Emulation

**Last verified:** 2026-03-28

This document is the canonical source of truth for understanding how the Universal Agent maps Anthropic model namespaces, routes queries through the Z.AI proxy, and guards against silent inference failures.

## 1. Z.AI Anthropic Proxy Emulation
Our system primarily uses Z.AI endpoint definitions mapped onto Anthropic SDK compatibility paths. Behind the scenes, we do not call standard Claude endpoints natively; we call Z.AI's `GLM` proxy models utilizing a standardized mapping logic.

The master resolution logic lives strictly in `src/universal_agent/utils/model_resolution.py`.

### The Core Model Map
We maintain a three-tier fallback logic for consistency, equating Anthropic nomenclature with GLM performance targets:

| Universal Agent Tier | Target Z.AI Proxy Model | Expected Use Cases |
| --- | --- | --- |
| **`opus`** | `GLM-5.1` | Heavy reasoning, deep-context orchestration. |
| **`sonnet`** | `GLM-5-Turbo` | Global Default. Code generation, fast proactive looping, tool invocation. |
| **`haiku`** | `GLM-4.5-Air` | Lightweight parsing, rapid summarization, data extraction. |

---

## 2. Global Resolution Target (`sonnet`)

Previously, various background workers and gateway classes arbitrarily fell back to `"opus"` or `"haiku"`, which created unexpected routing and scaling bottlenecks. 

### Enforced Global Default
The system now enforces **Sonnet (`GLM-5-Turbo`)** as the universal application-level baseline. 

If any module specifically attempts to resolve `_resolve_default_anthropic_model()` without explicit constraints, it retrieves `resolve_sonnet()`. 

Agent instantiations (like `health_evaluator`, `decomposition_agent`, `refinement_agent`, and `llm_classifier`) now all explicitly fetch their models via:
```python
from universal_agent.utils.model_resolution import resolve_sonnet
model = resolve_sonnet()
```

### Methods for Overriding
Should developers need to invoke different model weights, explicit overrides exist via three mechanisms:

1. **Environment Variables**:
   Defining any of the following environment keys will instantly override the hard-mapped values in `model_resolution.py`: 
   - `ANTHROPIC_DEFAULT_SONNET_MODEL`
   - `ANTHROPIC_DEFAULT_OPUS_MODEL`
   - `ANTHROPIC_DEFAULT_HAIKU_MODEL`
2. **Programmatic Choice**:
   Importing `resolve_claude_code_model()` or `resolve_model("opus")` / `resolve_model("haiku")` allows targeted sub-modules to jump out of the global sonnet constraint.
3. **Subagent SDK Assignments**:
   Inside an `AgentDefinition` YAML or Python dataclass, specifying `model: opus` will force the orchestrator to resolve to the heavy emulation tier exclusively for that subagent's execution lifetime.

---

## 3. "Loud" Failure vs Silent Inference Drop
Because the Universal Agent runs complex, persistent asynchronous loops, an isolated inference failure (like a Z.AI outage, 401 Auth drop, or recurring 500 server error) holds the potential to silently freeze the worker queues while retries stack up.

We mitigate this by hooking inference telemetry directly into the **Capacity Governor** (`src/universal_agent/services/capacity_governor.py`).

### Dual-State Capacity Governor
The governor's job natively was just to watch API Rate Limits (429s) and prevent us from stacking concurrent slots during backoffs. It has been enhanced to also act as an **Inference Watchdog**.

When LLM requests fail repeatedly or fatally:
1. `report_api_failure()` tracks connection errors, 401s, 403s, and 500-level service crashes.
2. If failures exceed the threshold (`consecutive_api_errors > 2` or `is_fatal==True`), the governor throws its readiness state into `api_down`.
3. The `HeartbeatService` immediately detects this disruption prior to sweeping its queues.

### The "Loud Action"
Instead of quietly writing to `stdout`, the system responds defensively:
- The Dispatch loop rejects new task claims (freezing operations to prevent corruption or stuck state).
- An `AgentEvent(type="system_alert")` is broadcast immediately over the WebSocket matrix carrying the payload: `CRITICAL INFERENCE DROP: [Reason]`.
- This ensures operators instantly visually witness that the agent's LLM engine is detached, rather than wondering why their commands aren't proceeding.
