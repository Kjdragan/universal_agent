# Architecture Strategy: Local-First vs. Remote Workbench

**Date:** December 21, 2025  
**Status:** **ADOPTED**  

## 1. Core Philosophy: "Local Brain, Remote Hands"

This document establishes the architectural standard for how the Universal Agent interacts with the Composio Workbench. It supplants previous experiments with "Smart Remote Agents" in favor of a simpler, more robust **Local-First** approach.

### The Principle
*   **The Brain (Local)**: All reasoning, planning, text processing, report generation, and decision-making happen in the **Local Environment** (where the agent runs).
*   **The Hands (Remote)**: The Composio Workbench is treated strictly as an **Environment for External Actions** and untrusted code execution.

## 2. Decision Matrix

| Capability | **Primary Owner (Local)** | **Secondary / Fallback (Remote)** | Reason |
| :--- | :--- | :--- | :--- |
| **Reasoning / Planning** | ✅ **YES** | ❌ NO | Maintain single state/context; easier debugging. |
| **Data Processing** | ✅ **YES** (Standard) | ⚠️ ONLY for Huge Data (>50MB) | Local processing avoids network latency and complexity. |
| **Report Generation** | ✅ **YES** | ❌ NO | Local string manipulation is faster/cheaper. |
| **Web Search** | ❌ NO | ✅ **YES** | Remote tools handle the external API connection. |
| **Auth / SaaS Actions** | ❌ NO | ✅ **YES** | Composio manages auth tokens securely remotely. |
| **Untrusted Code** | ❌ NO | ✅ **YES** | Safety sandbox for executing wild Python/Bash. |

## 3. Data Flow Strategy

### ❌ The "Trap" (Anti-Pattern)
Do **NOT** use the Remote Workbench as a temporary storage buffer for small data.
1.  Remote Tool (`sync_response_to_workbench=True`) -> Save to Remote File.
2.  Local Agent receives "File Saved" message.
3.  Local Agent calls `workbench_download` to get the data.
*   **Result**: Unnecessary latency, failure points (download config), and complexity.

### ✅ The Standard (Local-First Pattern)
**DEFAULT to direct data return.**
1.  Remote Tool (`sync_response_to_workbench=False`) -> Returns JSON directly.
2.  Local Agent receives full data immediately.
3.  Local Agent processes data in memory.
*   **Result**: Instant access, atomic transaction, no synchronization needed.

## 4. Implementation Guidelines

### System Prompt Instructions
The System Prompt must explicitly guide the agent to avoid the "Trap":
> "Do NOT set `sync_response_to_workbench=True` unless you expect massive data output (>5MB). Prefer direct data return for immediate local processing."

### Tool Configuration
*   **`workbench_download`**: Reserved for "Emergency" or "High Gravity" situations where a tool *forced* a file save (e.g., a PDF generation tool that only outputs to disk).
*   **`workbench_upload`**: Used only when an external API *requires* a public URL or remote file path (e.g., attaching a file to a Composio Gmail call).

## 5. Exception Cases (When to go Remote)
We use the Remote Workbench for processing ONLY when:
1.  **Massive Data**: The dataset is too large to fit in the Agent's Context Window or download efficiently (e.g., filtering a 1GB CSV).
2.  **Specialized Environment**: The task requires a specific Linux binary or environment not present locally (e.g., `ffmpeg` for video encoding, headless Chrome for complex scraping).
3.  **Output Requirement**: An API specifically requires a file path on the remote filesystem (rare).

## 6. Workflow: Emailing Reports (Attachments)

To send an email with an attachment (e.g., HTML report), follow this robust pattern:

1.  **Generate Locally**: Create the report file in the local workspace (`local_toolkit.write_local_file`).
2.  **Upload to Cloud**: Use `local_toolkit.upload_to_composio` to "teleport" the file to the cloud.
    *   Input: Local absolute path.
    *   Output: JSON with `s3_key`.
3.  **Send Email**: Use `GMAIL_SEND_EMAIL` with the `s3_key`.
    *   `attachments=[{"s3_key": "user/uploads/..."}]`

## 7. Summary
We optimize for **simplicity and speed**. If the data *can* exist in the local context, it *should*. We do not simulate a remote workspace when we have a perfectly good one right here.
