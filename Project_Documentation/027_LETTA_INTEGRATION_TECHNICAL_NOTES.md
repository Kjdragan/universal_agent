# Letta Integration: Technical Notes
**Date:** 2026-01-04
**Reference File:** `sitecustomize.py`

This document outlines the custom runtime patches ("monkey patches") applied to the `agentic_learning` SDK to ensure stability and correct functionality within the Universal Agent environment.

## Active Monkey Patches

The following patches are automatically applied at runtime via `sitecustomize.py`.

### 1. Memory Client Upsert Fix (`_patch_letta_memory_upsert`)
**Target:** `agentic_learning.client.memory.client.MemoryClient.upsert`
**Issue:**  Fixes an `UnboundLocalError`. This is likely the "typo" issue referred to, where a variable was referenced before assignment in the original SDK code during memory block updates.
**Fix:** Replaces the `upsert` method with a fixed version (`_fixed_upsert`) that safely retrieves the agent object and handles the block update logic without scope errors.

### 2. Context Logic Fallback (`_patch_letta_context_fallback`)
**Target:** `agentic_learning.core.get_current_config` and Context Managers
**Issue:** Background tasks and async operations often lose the thread-local context required by the Letta SDK.
**Fix:** Implements a fallback mechanism (`_UA_LETTA_FALLBACK_CONFIG`) to persist configuration across context switches, ensuring `get_current_config()` returns valid data even in background threads.

### 3. Claude Stream Flushing (`_patch_letta_claude_stream_flush`)
**Target:** `agentic_learning.interceptors.claude.ClaudeInterceptor`
**Issue:** The default interceptor might trigger saving conversation turns only on connection close, which can be unreliable for long-running streams or specific 'result' message types.
**Fix:** Wraps the message iterator to explicitly flush (save) the conversation turn immediately upon receiving a "result" message, ensuring data is captured even if the stream remains open or closes unexpectedly.

## Configuration
These patches are controlled via environment variables (defaulting to `1`/`true`):
*   `UA_LETTA_UPSERT_PATCH`
*   `UA_LETTA_CONTEXT_FALLBACK`
*   `UA_LETTA_CLAUDE_STREAM_PATCH`
