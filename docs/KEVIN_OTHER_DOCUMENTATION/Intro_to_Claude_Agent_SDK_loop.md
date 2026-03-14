
The Definitive Guide to the Claude Agent SDK: Building the Next Generation of AI
Bragi


The age of AI that just talks is over. The age of AI that acts has begun.

But as every engineer who has moved beyond a simple demo knows, the real challenge isn’t making an agent that can act. It’s making an agent that can be trusted. It’s managing the chaotic reality of tools, permissions, costs, and long-running tasks in a production environment where failure is not an option.

This is not just another guide to the Claude Agent SDK. This is a deep, comprehensive dive into the architecture, security models, and production best practices required to build the next generation of enterprise-grade AI agents.

We’ll dissect its architecture, the comprehensive built-in toolset, the multi-layered security model, the groundbreaking Opus 4.6 engine, and the hard-won lessons from real-world deployments.

For architects, engineers, and leaders building the next generation of AI.

Settle in. This is the deep dive you’ve been looking for.

#AI #Agents #Claude #Anthropic #SoftwareEngineering #LLM #AgentSDK

Part 1: Introduction — The Shift from Conversation to Execution
The artificial intelligence landscape is undergoing a seismic shift. The initial wave of excitement, driven by large language models (LLMs) packaged as conversational chatbots, has given way to a more profound and pragmatic pursuit: creating autonomous agents that can execute complex tasks, interact with digital environments, and deliver tangible outcomes. The era of simply asking an AI a question and receiving a text response is giving way to the era of instructing an AI to do something and watching it execute.

This is the context in which the Claude Agent SDK was born. It is Anthropic’s open-source, production-grade framework that exposes the same battle-tested infrastructure powering Claude Code as a programmable library for building autonomous AI agents in Python and TypeScript. It was first announced on May 22, 2025, alongside Claude Opus 4 and Sonnet 4, under the name “Claude Code SDK.” Its original pitch was simple: extend Claude Code’s coding capabilities programmatically.

But internally, Anthropic discovered something bigger. As their engineering blog explained:

“Over the past several months, Claude Code has become far more than a coding tool. At Anthropic, we’ve been using it for deep research, video creation, and note-taking, among countless other non-coding applications.”

On September 29, 2025, alongside the launch of Claude Sonnet 4.5, Anthropic renamed it to the Claude Agent SDK. The rebrand was a signal: this was no longer just a tool for coders. It was a general-purpose agent framework for building finance bots, research agents, customer support systems, marketing automation, and any autonomous workflow imaginable.

1.1. The Migration: What Changed
The rename was more than cosmetic. It included breaking changes to package names and imports that developers needed to address.

Press enter or click to view image in full size

Three critical behavioral changes were introduced in v0.1.0: the SDK no longer uses Claude Code’s system prompt by default (you must opt in); it no longer reads filesystem settings (CLAUDE.md, settings.json) by default (you must set settingSources); and ClaudeCodeOptions was renamed to ClaudeAgentOptions.

1.2. Why This is Not Just Another API Wrapper
The distinction between the Claude Agent SDK and the standard Anthropic API SDK is fundamental. The standard SDK (anthropic / @anthropic-ai/sdk) provides stateless message completion: you send messages, get responses, and handle everything else yourself. The Agent SDK provides the full agent runtime: built-in tools, automatic context management, session persistence, fine-grained permissions, subagent orchestration, and MCP extensibility.

The runtime architecture is a three-layer stack:

Your Application (Python / TypeScript)
        ↓
Claude Agent SDK (The Agent Harness)
        ↓
Claude Code CLI (The Runtime Engine, bundled with the SDK)
        ↓
Claude API (The Model)
Conceptual layering (implementation may vary by platform)

This layering is not cosmetic. It explains why the SDK is so powerful out-of-the-box: you are not building tool execution plumbing from scratch. You are controlling an already-integrated, production-proven tool suite and agent loop.

1.3. Installation and Setup
Getting started is straightforward. The Python SDK automatically bundles the Claude Code CLI, so no separate installation is required.

Python:

pip install claude-agent-sdk
TypeScript:

npm install @anthropic-ai/claude-agent-sdk
Prerequisites: Python 3.10+ (supports up to 3.13), Node.js 18+, and macOS, Linux, or WSL on Windows.

Authentication: The simplest path uses an Anthropic API key (export ANTHROPIC_API_KEY=your-key). For enterprise deployments, the SDK also supports Amazon Bedrock (CLAUDE_CODE_USE_BEDROCK=1), Google Vertex AI (CLAUDE_CODE_USE_VERTEX=1), and Microsoft Azure AI Foundry (CLAUDE_CODE_USE_FOUNDRY=1).

At the time of writing (early Feb 2026), the latest versions are v0.1.34 for Python and v0.2.37 for TypeScript (with over 1.85M weekly downloads).

Part 2: The Core Architecture — Giving Agents a Computer
To truly understand the Claude Agent SDK, one must appreciate its core philosophy: it is designed to give agents a computer, not just a prompt. This is not a mere API wrapper for generating text; it is a comprehensive runtime environment that provides Claude with direct, controlled access to a terminal, a file system, and the web. This architectural choice enables the iterative loop of gather context → take action → verify work → repeat that is the hallmark of genuine autonomy.

2.1. The Agentic Loop as a First-Class Abstraction
The entire SDK is built around a feedback loop that mirrors how a human developer works. Instead of a single, stateless request-response, the SDK orchestrates a continuous cycle where the agent:

Gathers Context: Reads files, searches the web, or inspects its environment.
Thinks & Plans: Reasons about the goal and forms a plan of action.
Takes Action: Calls a tool, such as executing a shell command or editing a file.
Observes the Result: Analyzes the output of the tool to see if the action was successful.
Verifies & Repeats: Checks the work against the goal and decides on the next step, continuing the loop until the task is complete.
This loop is exposed to the developer not as a messy stream of raw text, but as a clean, structured sequence of strongly-typed messages. This is a critical engineering decision. It means you can build reliable UI, logging, and guardrails by listening for specific events (ToolUseBlock, ThinkingBlock, ResultMessage) rather than attempting to parse the model's free-form thoughts.

Press enter or click to view image in full size

2.2. The Two Interfaces: query() vs. ClaudeSDKClient
The SDK provides two primary entry points tailored for different levels of complexity:

query() (Stateless, One-Shot): This is the simplest way to use the SDK. It creates a new, ephemeral session for each call and returns an AsyncIterator of messages. It's ideal for straightforward, single-purpose tasks, command-line tools, and CI/CD pipeline steps where session history is not required. Because it has a ~12-second startup overhead due to process initialization, it is less suitable for interactive, low-latency applications.
ClaudeSDKClient (Stateful, Bidirectional): For complex, multi-turn conversations, the ClaudeSDKClient class (in Python) provides a stateful, persistent interface. It allows you to manage a long-running session, interrupt the agent, use custom tools and hooks, and maintain continuity across multiple user interactions. The TypeScript SDK achieves similar functionality through its extended Query object, which includes methods like interrupt() and rewindFiles().
Python query() Example:

import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions

async def main():
    options = ClaudeAgentOptions(
        system_prompt="You are an expert Python developer",
        permission_mode="acceptEdits",
        cwd="/home/user/project",
    )

    async for message in query(
        prompt="Create a Python web server with health checks",
        options=options,
    ):
        print(message)

asyncio.run(main())
2.3. The ClaudeAgentOptions Object: The Master Control Panel
Virtually every aspect of an agent’s behavior is configured through the ClaudeAgentOptions object. This centralized configuration is one of the SDK's greatest strengths, providing a single place to define everything from security policies to subagent behavior. Below is a comprehensive table of the most critical options.

Press enter or click to view image in full size

2.4. The Message Stream: A Rich Telemetry Feed
As the agent executes, the SDK doesn’t just stream back text; it yields a rich, strongly-typed series of messages that provide a complete transcript of the agent’s work. This allows for robust, programmatic interaction with the agent’s process.

The most important message types are:

SDKAssistantMessage: Contains Claude's responses, which are composed of different content blocks:

TextBlock: The agent's textual reasoning or final answer.
ThinkingBlock: An optional block showing the agent's thought process before taking an action.
ToolUseBlock: A structured request to call a specific tool with specific arguments.
ToolResultBlock: The result of a tool execution, fed back into the loop.
SDKResultMessage: This is the final message in the stream, signaling the end of the query. It is a treasure trove of telemetry, containing:

subtype: The reason the query ended (success, error_max_turns, error_max_budget_usd, etc.).
total_cost_usd: The total cost of the query in USD.
modelUsage: A detailed, per-model breakdown of input/output tokens and cache statistics.
duration_ms: The total duration of the query.
num_turns: The number of conversational turns taken.
SDKCompactBoundaryMessage: An informational message emitted when the context window is nearing its limit and automatic compaction has occurred.

This structured, event-driven approach is fundamental to the SDK’s design. It transforms agent interaction from a guessing game of parsing unstructured text into a deterministic process of handling well-defined events, making it possible to build reliable, production-grade applications on top.

Part 3: The Agent’s Toolbox — The Complete Guide to All 18+ Built-in Tools
The power of a Claude agent comes from its ability to interact with the world. The SDK provides a comprehensive suite of over 18 built-in tools that give the agent direct access to a computer’s core functionalities. This is not a simulation; when an agent uses the Bash tool, it is executing a real shell command in a sandboxed environment. Understanding this toolbox is essential to understanding what agents can achieve.

Below is the definitive reference for every built-in tool, its purpose, and its input/output schema.

3.1. Filesystem Operations
These tools allow the agent to read, search, and modify the filesystem within its working directory.

Read: Reads the content of a file. It can handle plain text, images (returning base64 data), PDFs (returning per-page text and images), and Jupyter notebooks (returning cell-by-cell content).

Input: { file_path: string; offset?: number; limit?: number }
Best Practice: Use for inspecting files before editing.
Write: Creates or overwrites a file with new content.

Input: { file_path: string; content: string }
Output: { message: string; bytes_written: number; file_path: string}
Best Practice: Use for creating new files or replacing entire file contents.
Edit: Performs a precise, string-based search-and-replace within a file.

Input: { file_path: string; old_string: string; new_string: string; replace_all?: boolean }
Output: { message: string; replacements: number; file_path: string }
Best Practice: Ideal for targeted code modifications, like changing a variable name or updating a dependency version.
MultiEdit: Applies multiple, non-overlapping edits to a file in a single operation.

Input: { file_path: string; edits: Array<{ old_string: string; new_string: string }> }
Best Practice: Use for complex refactoring that requires several coordinated changes in one file.
Glob: Finds files and directories matching a glob pattern (e.g., **/*.py). Results are sorted by modification time.

Input: { pattern: string; path?: string }
Output: { matches: string[]; count: number; search_path: string }
Best Practice: The primary tool for discovering the structure of a codebase.
Grep: Performs a fast, regex-powered search over file contents, built on the high-performance ripgrep engine.

Input: { pattern: string; path?: string; glob?: string; ... } (supports numerous ripgrep flags)
Best Practice: Use for locating specific code snippets, error messages, or configurations across a project.
3.2. Code Execution
These tools give the agent the ability to run commands and scripts.

Bash: Executes a shell command.

Input: { command: string; timeout?: number; run_in_background?: boolean }
Output: { output: string; exitCode: number; shellId?: string }
Best Practice: The workhorse tool for everything from running tests (npm test) to installing dependencies (pip install -r requirements.txt).
BashOutput: Retrieves the output from a command that was started in the background.

Input: { bash_id: string; filter?: string }
Output: { output: string; status: 'running' | 'completed' | 'failed'; exitCode?: number }
Best Practice: Use for monitoring long-running processes like a web server or a build script.
KillBash: Terminates a background shell process.

Input: { bash_id: string }
Best Practice: Essential for cleaning up resources and stopping runaway processes.
3.3. Web Interaction
These tools connect the agent to the public internet.

WebSearch: Performs a web search and returns a list of results.

Input: { query: string; allowed_domains?: string[]; blocked_domains?: string[] }
Output: { results: Array<{ title: string; url: string; snippet: string }> }
Best Practice: The starting point for any research task that requires up-to-date information.
WebFetch: Fetches the content of a specific URL and uses an AI model to process it based on a given prompt.

Input: { url: string; prompt: string }
Output: { response: string; url: string; ... }
Best Practice: Use for extracting specific information from a known webpage, like summarizing an article or pulling data from a table.
3.4. Orchestration & Human-in-the-Loop
These tools manage the agent’s own workflow and allow it to interact with the user.

Task: Delegates a subtask to a specialized subagent.

Input: { description: string; prompt: string; subagent_type: string }
Output: { result: string; usage?: TokenUsage; total_cost_usd?: number; ... }
Best Practice: The core mechanism for building multi-agent systems. (See Part 6 for a deep dive).
AskUserQuestion: Pauses execution and prompts the user for clarification or a decision.

Input: { questions: Array<{ question: string; options: Array<{ label: string }> }> }
Best Practice: Essential for building interactive agents that require human approval at critical junctures.
TodoWrite: Allows the agent to maintain a structured checklist of its progress.

Input: { todos: Array<{ content: string; status: 'pending' | 'in_progress' | 'completed' }> }
Best Practice: A simple but powerful tool for improving the reliability of complex, multi-step tasks.
ExitPlanMode: Used when an agent is in plan mode to signal that it has finished planning and is ready for user approval to execute.

Best Practice: The formal handoff mechanism in planning-focused workflows.
3.5. Specialized Tools
NotebookEdit: Provides specialized functionality for editing cells within a Jupyter notebook (.ipynb) file.

Input: { notebook_path: string; cell_id?: string; new_source: string; ... }
Best Practice: Use for data science and research workflows that involve notebooks.
3.6. Metaprogramming & MCP
ListMcpResources / ReadMcpResource: Tools that allow an agent to inspect the custom tools and resources available to it from connected MCP servers.

Best Practice: Enables agents to be more self-aware and dynamically adapt to the tools at their disposal.
3.7. Table of Default Permissions
Not all tools are created equal in terms of risk. The SDK categorizes tools and requires user permission by default for any action that could have side effects.

Press enter or click to view image in full size

This default-deny posture is a cornerstone of the SDK’s security model, forcing developers to make conscious decisions about the level of autonomy they grant their agents. The next section explores this security model in its entirety.

Part 4: The Security Model — Building Trustworthy Agents
An autonomous agent with access to a computer is powerful, but also presents significant security risks. Anthropic has engineered the Agent SDK with a defense-in-depth security model that is one of its most critical and differentiating features. Rather than a simple on/off switch, it provides a sophisticated, multi-layered system for enforcing policy, controlling execution, and minimizing risk. Building trustworthy agents starts here.

4.1. The Threat Model: From Prompt Injection to Runaway Costs
The SDK’s security architecture is designed to directly mitigate a clear set of threats inherent to autonomous agents:

Prompt Injection: The risk that an agent, processing untrusted content from a file or webpage, could be manipulated into performing malicious actions. The defense is a combination of strict tool permissions and hooks that can inspect and sanitize inputs.
Data Exfiltration: The risk that an agent could access sensitive local files and send them to an external server. The defense is the network sandbox, which blocks unauthorized outbound connections.
Destructive Operations: The risk that an agent could execute a destructive command like rm -rf /. The defense is a combination of permission prompts, declarative rules, and OS-level sandboxing that restricts file access.
Runaway Execution & Cost: The risk that an agent could enter an infinite loop, consuming vast amounts of time and money. The defense is the max_turns and max_budget_usd settings, which act as hard circuit breakers.
4.2. The Multi-Layered Evaluation Order: A Funnel of Trust
When an agent attempts to use a tool, its request passes through a strict, ordered funnel of checks. Understanding this order is crucial for implementing effective security policies.

PreToolUse Hook: The very first check. A programmatic hook that can immediately allow, deny, or modify the tool call. This is the most powerful level of control.
Declarative Deny Rules: The agent checks disallowed_tools in ClaudeAgentOptions and deny rules in settings.json.
Declarative Allow Rules: If not denied, the agent checks allowed_tools and allow rules.
Declarative Ask Rules: If not explicitly allowed or denied, the agent checks ask rules.
Permission Mode: If the declarative rules don’t resolve the request, the configured permission_mode (default, acceptEdits, etc.) is applied.
canUseTool Callback: If the mode is default or the action is still ambiguous, this programmatic callback makes the final runtime decision.
PostToolUse Hook: After the tool has executed, this hook can inspect the result for auditing, logging, or triggering subsequent workflows.
This layered approach allows you to combine broad, static policies (declarative rules) with fine-grained, context-aware logic (hooks and callbacks).

4.3. The Four Permission Modes
The permission_mode option sets the agent's default level of autonomy:

default: The safest mode. The agent prompts the user for approval on every sensitive operation.
acceptEdits: A more permissive mode that automatically approves file modifications (Edit, Write) and common filesystem commands (mkdir, rm, mv). It still prompts for other actions like Bash.
plan: A read-only mode. The agent can use tools to research and gather information, but it cannot execute any action that would modify the environment. It will use the ExitPlanMode tool when it is ready for the user to approve its plan.
bypassPermissions: The most dangerous mode. It skips all permission checks. For safety, this mode requires an explicit opt-in flag (allowDangerouslyBypassPermissions: true) and should be used with extreme caution, primarily in fully-sandboxed CI/CD environments.
4.4. Programmatic Control: The canUseTool Callback
For maximum flexibility, the canUseTool callback allows you to implement any custom permission logic you can imagine. The callback receives the tool's name and its input arguments, and can programmatically decide to allow, deny, or even modify the call on the fly.

Example: Blocking rm -rf and redirecting file writes

const result = query({
  prompt: "Clean up the temp directory and save the logs.",
  options: {
    permissionMode: "default",
    canUseTool: async (toolName, input) => {
      // Rule 1: Absolutely deny any attempt to use 'rm -rf'
      if (toolName === "Bash" && typeof input.command === 'string' && input.command.includes("rm -rf")) {
        return { behavior: 'deny', message: 'Destructive commands are permanently blocked.' };
      }

      // Rule 2: Redirect all file writes to a sandboxed 'output' directory
      if (toolName === "Write" && typeof input.file_path === 'string' && !input.file_path.startsWith("output/")) {
        const originalPath = input.file_path;
        const newPath = `output/${originalPath.split('/').pop()}`;
        const updatedInput = { ...input, file_path: newPath };
        return { behavior: 'allow', updatedInput: updatedInput };
      }

      // Default behavior: allow the action
      return { behavior: 'allow', updatedInput: input };
    }
  }
});
4.5. Declarative Guardrails and Fine-Grained Matching
While hooks are powerful, many policies can be expressed more simply in configuration. The allowedTools and disallowedTools options support fine-grained prefix matching, allowing you to define precise rules without writing code.

Get Bragi’s stories in your inbox
Join Medium for free to get updates from this writer.

Enter your email
Subscribe
Example: Allowing only specific git commands

# Allow the agent to check status and diffs, but not commit or push.
--allowedTools "Bash(git status),Bash(git diff *),Read(*),Glob(*)"
This level of control is also available in the project-level settings.json file, which provides a clear, auditable record of the agent's permissions.

4.6. OS-Level Sandboxing: Bubblewrap and Seatbelt
Perhaps the most robust layer of security is the OS-level sandbox, which is enabled by default. The SDK uses Bubblewrap on Linux and Seatbelt on macOS to create an isolated environment for the agent. This sandbox enforces two critical boundaries:

Filesystem Isolation: The agent’s filesystem access is restricted to its current working directory (cwd) and a few other necessary paths. It cannot read your ~/.ssh directory or other sensitive user files.
Network Isolation: All outbound network requests are routed through an internal proxy that enforces domain restrictions, preventing the agent from communicating with unauthorized servers.
This sandboxing is so effective that Anthropic reports it reduces the need for manual permission prompts by 84%. You can configure the agent to automatically allow Bash commands when the sandbox is enabled (autoAllowBashIfSandboxed), striking a balance between security and autonomy.

4.7. Network Controls & The Proxy Pattern
For production deployments, it is a best practice to manage API credentials and monitor traffic outside the agent’s security boundary. The SDK supports this through standard proxy environment variables (HTTP_PROXY, HTTPS_PROXY) and a specific ANTHROPIC_BASE_URL variable. This allows you to route all API traffic through a trusted proxy (like Envoy or a custom server) that can inject credentials, log requests, and enforce organization-wide policies before the traffic ever reaches Anthropic's servers. This pattern ensures that the agent itself never has direct access to sensitive API keys.

4.8. The Hooks System: 12 Lifecycle Events
Beyond the permission evaluation funnel, the SDK provides a comprehensive hooks system that allows developers to intercept and react to events throughout the agent’s lifecycle. The Python SDK supports 6 lifecycle hooks, while the TypeScript SDK extends this to 12 lifecycle events, providing even more granular control.

Press enter or click to view image in full size

Hooks can be defined in three ways:

Command hooks: Execute a shell command and use the exit code to determine the action (e.g., exit 0 = allow, exit 2 = deny).
Prompt hooks: Use a single-turn LLM evaluation to decide whether to allow or deny a tool call.
Agent hooks: Spawn a multi-turn subagent to perform a thorough verification before allowing a tool call.
Example: A filesystem-based hook in .claude/settings.json

{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "type": "command",
        "command": "python3 /scripts/validate_bash_command.py \"$TOOL_INPUT\""
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write",
        "type": "prompt",
        "prompt": "Review this file write operation. Does it contain any sensitive data like API keys or passwords? If yes, respond DENY. Otherwise, respond ALLOW."
      }
    ]
  }
}
This hooks system, combined with the permission evaluation funnel, gives developers an unprecedented level of control over agent behavior, making it possible to build agents that are both powerful and trustworthy.

Part 5: Extensibility — Teaching Your Agent New Tricks
While the built-in tools provide a powerful foundation, the true potential of the Agent SDK is realized through its rich extensibility model. The framework is designed to be augmented, allowing you to connect your agent to any internal API, third-party service, or proprietary knowledge base. This is achieved through a layered system of protocols, tools, and skills.

5.1. MCP: The Model Context Protocol
The bedrock of the SDK’s extensibility is the Model Context Protocol (MCP), an open standard created by Anthropic for connecting agents to tools and data sources. It defines a standardized way for an agent to discover the tools an external server provides and then invoke them. The SDK has native, first-class support for MCP and can interact with MCP servers through four different transport types:

stdio: For running a local MCP server as a subprocess, communicating over standard input/output. This is ideal for tools written in other languages.
http / sse: For connecting to remote MCP servers over the network. http is for standard request/response, while sse (Server-Sent Events) is for streaming responses.
sdk: The most powerful and efficient method. It allows you to run an MCP server inside your application's process, eliminating all network and subprocess overhead. This is the recommended approach for custom tools written in Python or TypeScript.
Example: Connecting to a remote HTTP MCP server

mcpServers: {
  "internal_docs": {
    type: "http",
    url: "https://internal-docs.my-company.com/mcp",
    headers: { "Authorization": "Bearer ..." }
  }
}
Once connected, the tools from this server become available to the agent under the namespace mcp__internal_docs__<tool_name>.

5.2. Creating Custom Tools: In-Process MCP Servers
The most direct way to extend the agent’s capabilities is by creating custom tools using the in-process sdk MCP server. This allows you to expose your own Python or TypeScript functions directly to the agent.

Python Example: A custom tool to query a database

from claude_agent_sdk import tool, create_sdk_mcp_server, ClaudeAgentOptions, query
import database

# Define the tool with a name, description, and a schema for its arguments
@tool("query_database", "Query the company's user database", {"sql_query": str} )
async def query_database(args):
    try:
        results = await database.run_query(args["sql_query"])
        # Return the results in the structured format the agent expects
        return {"content": [{"type": "text", "text": f"Query Result: {results}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}]}

# Create an in-process MCP server to host the tool
db_server = create_sdk_mcp_server(
    name="database", version="1.0.0", tools=[query_database]
)

# Configure the agent to use the server and allow the new tool
options = ClaudeAgentOptions(
    mcp_servers={"db": db_server},
    allowed_tools=["mcp__db__query_database"]
)
TypeScript Example: Using Zod for schema validation

import { tool, createSdkMcpServer } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";

const sendEmailTool = tool("send_email", "Send an email to a user",
  // Use a Zod schema to define and validate the input
  z.object({ 
    recipient: z.string().email(), 
    subject: z.string(),
    body: z.string()
  }),
  // The implementation of the tool
  async (args) => {
    await email_service.send(args.recipient, args.subject, args.body);
    return { content: [{ type: "text", text: `Email sent to ${args.recipient}` }] };
  }
);

const emailServer = createSdkMcpServer({ name: "email", tools: [sendEmailTool] });
5.3. The “1000 Tools” Problem: Automatic Tool Search
As the number of custom tools grows, a new problem emerges: the definitions of all those tools can consume a significant portion of the agent’s precious context window. The SDK has an elegant, built-in solution for this. When the total size of tool definitions exceeds 10% of the context window, the SDK automatically activates a Tool Search Tool. Instead of preloading all tool definitions, the agent can now dynamically search for the right tool on-demand using regex or BM25-based search, loading only the definitions it needs. This allows an agent to scale to hundreds or even thousands of available tools without degrading its performance.

5.4. Agent Skills: The Reusable Knowledge Layer
While custom tools provide the agent with new abilities, Agent Skills provide it with new knowledge and workflows. A skill is an organized package of instructions, scripts, and reference material that teaches an agent how to perform a complex, multi-step task. Published as an open standard at agentskills.io in December 2025, skills work identically across Claude.ai, the Claude Code terminal, and the Agent SDK.

Every skill is a directory containing a SKILL.md file, which defines the skill's name, description, and the instructions for the agent.

Example SKILL.md for a pdf-processing skill:

---
name: pdf-processing
description: Extract and analyze content from PDF documents
---

# PDF Processing Workflow

## Instructions
1. When asked to process a PDF, first use the `pdftotext` utility available in the `Bash` tool to convert the PDF to a text file.
2. Read the resulting text file.
3. Analyze the extracted text to identify the key data points requested by the user.
4. Generate a structured summary of your findings in Markdown format.
Skills use a powerful technique called progressive disclosure. At startup, the SDK only loads the name and description of each available skill, consuming a minimal amount of context. Only when the agent decides to use a specific skill does it read the full SKILL.md file. This makes the agent's effective knowledge base nearly unbounded.

To use skills, you must enable them in the agent’s options:

options = ClaudeAgentOptions(
    # The SDK must be told to load settings from the filesystem
    setting_sources=["user", "project"], 
    # The "Skill" tool itself must be allowed
    allowed_tools=["Skill", "Read", "Write", "Bash"]
)
5.5. Plugins & Slash Commands: Packaging and UI
To make extensions easier to manage and distribute, the SDK supports a plugin system. A plugin is simply a directory that bundles together skills, custom slash commands, hooks, and MCP servers. You can load multiple local plugins via the plugins option.

Slash commands (e.g., /review) are user-facing shortcuts that trigger complex workflows. In the latest version of the SDK, the slash command system has been merged with Agent Skills; creating a skill automatically creates a corresponding slash command. This provides a clean, unified system for extending the agent's capabilities and providing a user-friendly interface to them.

Part 6: Multi-Agent Systems — Orchestrating Agent Teams
Many complex problems are too large or multifaceted for a single agent to solve alone. The Claude Agent SDK provides a robust framework for hierarchical multi-agent workflows, allowing a primary agent to act as an orchestrator that delegates specific subtasks to a team of specialized subagents. This pattern is a cornerstone of building sophisticated, production-grade agentic systems.

6.1. The Subagent Pattern: Isolate, Parallelize, Summarize
The subagent pattern is designed to solve several key challenges in agent engineering:

Context Isolation: Each subagent runs in its own, completely separate context window. This prevents the main agent’s context from becoming bloated with the detailed work of a subtask and allows the subagent to focus entirely on its specific goal without being distracted by the larger conversation.
Parallel Execution: The orchestrator can spawn multiple subagents to work on different parts of a problem concurrently. For example, one subagent could be researching a topic on the web while another analyzes a local data file.
Distilled Summaries: A subagent is designed to perform a complex task and then return only a concise, relevant summary of the result to the parent agent. This is a powerful form of programmatic context management, ensuring that the orchestrator receives only the signal, not the noise.
6.2. Defining and Invoking Subagents
Subagents are defined programmatically within the ClaudeAgentOptions object. The orchestrator agent then uses the built-in Task tool to invoke a subagent whenever it determines that a task matches the subagent's description.

Example: An orchestrator with code-reviewer and debugger subagents

from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

async for message in query(
    prompt="Please review the new authentication module for security flaws and then run the test suite to check for regressions.",
    options=ClaudeAgentOptions(
        agents={
            # Definition for the first subagent
            "code-reviewer": AgentDefinition(
                description="An expert code reviewer who specializes in finding security vulnerabilities and deviations from best practices. Use this proactively after any code changes.",
                prompt="You are a senior code reviewer. Your sole focus is on security and code quality. Scrutinize the provided files for any potential issues.",
                tools=["Read", "Grep", "Glob"], # This subagent has a limited toolset
                model="claude-sonnet-4-5" # Use a balanced model for this task
            ),
            # Definition for the second subagent
            "debugger": AgentDefinition(
                description="A debugging specialist for analyzing errors and test failures. Use this when tests fail or an error is encountered.",
                prompt="You are an expert debugger. Analyze the provided error logs and test failures, identify the root cause, and suggest a fix.",
                model="claude-haiku-4-5" # Use a fast, cheap model for this focused task
            )
        },
        # CRITICAL: The parent agent must have permission to use the 'Task' tool
        allowed_tools=["Read", "Edit", "Bash", "Task"]
    )
):
    print(message)
In this example, when the orchestrator receives the prompt, it will first recognize that the “review the new authentication module” part of the request matches the description of the code-reviewer subagent. It will then use the Task tool to spawn that subagent and pass it the relevant files. After the reviewer returns its findings, the orchestrator will proceed to the second part of the request, run the tests using its own Bash tool, and if they fail, it will invoke the debugger subagent to analyze the failure.

6.3. The Rules of Hierarchy
The SDK’s multi-agent model is strictly hierarchical, which imposes several important rules:

No Infinite Nesting: Subagents cannot spawn their own subagents. This prevents the risk of infinite recursion and keeps the orchestration logic clean and manageable.
Permission Inheritance: Subagents inherit certain settings from their parent, most critically the bypassPermissions mode. If the parent is in this mode, all subagents will be as well, and this cannot be overridden. This is a crucial security consideration.
Model Flexibility: As shown in the example, you can assign different models to different subagents. This allows you to use a powerful, expensive model like Opus for the main orchestrator while using cheaper, faster models like Sonnet or Haiku for more focused, high-volume subtasks, optimizing both performance and cost.
6.4. Filesystem-Based and Built-in Subagents
In addition to programmatic definitions, subagents can also be defined as Markdown files with YAML frontmatter in the .claude/agents/ directory. This is useful for creating reusable, project-specific agents.

The SDK also ships with several built-in subagent types that can be used out of the box, including:

Explore: A fast, read-only subagent designed for quickly searching and understanding a codebase.
Plan: A research-focused subagent used for gathering context and creating a plan before execution begins.
This hierarchical, task-delegation model provides a structured and scalable approach to building complex systems. It allows you to compose agents from specialized, single-responsibility components, leading to more reliable, maintainable, and cost-effective solutions than a single, monolithic agent attempting to do everything itself.

Part 7: The Engine Room — A Deep Dive into Opus 4.6
The most sophisticated agent framework is only as good as the model that powers it. The Claude Agent SDK is designed to work best with Anthropic’s flagship model series, and the release of Claude Opus 4.6 on February 5, 2026, represents a quantum leap in the capabilities of the agents you can build.

7.1. Core Capabilities & Benchmarks
Opus 4.6 is not an incremental update; it is a new architecture focused on agentic task performance. It demonstrates state-of-the-art results on complex reasoning and problem-solving benchmarks, achieving a remarkable 76% on the MRCR v2 benchmark (a long-context retrieval test with 1M tokens and 8 needles), and showing significant improvements on graduate-level reasoning benchmarks like GDPval-AA. This model was designed from the ground up to excel at using tools, following complex instructions, and maintaining coherence over long, multi-step tasks.

7.2. Agent Teams: The C Compiler Experiment
The most dramatic demonstration of Opus 4.6’s agentic power was a widely reported experiment. A team of 16 Claude agents, orchestrated by the Agent SDK, was tasked with building a functional C compiler from scratch. The agents reportedly worked for weeks, autonomously delegating tasks, writing code, running tests, and resolving conflicts. The project, which ultimately cost around $20,000 in API fees, was a resounding success and proved that the combination of a powerful model and a robust agent framework could tackle long-horizon tasks of a complexity previously thought to be years away.

7.3. Adaptive Thinking & The “Effort” Knob
One of the most innovative features of Opus 4.6 is Adaptive Thinking. The model can dynamically decide how much computational “effort” to apply to a given problem. For a simple request, it might generate a response quickly and cheaply. For a complex reasoning or coding task, it will automatically engage a more intensive thought process to arrive at a higher-quality solution. This is analogous to a human developer deciding to take a quick look at a simple bug versus spending hours whiteboarding a complex architectural problem.

While this process is largely automatic, developers can influence it. The SDK exposes a max_thinking_tokens parameter, and future versions are expected to provide more direct control over this "effort" knob, allowing developers to explicitly balance speed, cost, and intelligence for different tasks.

7.4. Pricing Models: Standard, Fast Mode, and 1M Context
Opus 4.6 introduces a more nuanced pricing structure that reflects its flexible capabilities. Understanding this is key to managing the cost of your agents.

Press enter or click to view image in full size

This tiered pricing, combined with the ability to assign different models to different subagents, gives architects a powerful set of tools for optimizing the cost-performance ratio of their multi-agent systems.

Part 8: The Memory Problem — Sustaining Coherence in Long-Running Tasks
One of the most significant challenges in building useful agents is the “memory problem.” How can an agent maintain a coherent understanding of its goal and progress over tasks that might last for hours or even days, far exceeding the limits of any model’s context window? The Claude Agent SDK addresses this through a sophisticated, multi-layered approach to context management and persistence.

8.1. Automatic Compaction: The First Line of Defense
The SDK’s primary strategy for managing long conversations is server-side compaction. As the conversation approaches the model’s context limit (e.g., 200K tokens), the SDK will automatically summarize the oldest parts of the conversation, replacing them with a condensed, machine-written summary. This process is seamless and allows an agent to continue working on a task for extended periods — 30+ hours of sustained operation have been observed with Sonnet 4.5.

This feature is currently in beta and can be enabled with the compact-2026-01-12 flag. When compaction occurs, the SDK fires a PreCompact hook, giving you a chance to save the full, un-compacted transcript for auditing purposes. It also emits a SDKCompactBoundaryMessage into the message stream, making the process fully observable.

For advanced use cases, you can even use a SessionStart hook with a compact matcher to re-inject critical instructions or reminders into the context immediately after a compaction event, ensuring the agent stays on track.

8.2. Context Editing: Surgical Precision
While compaction is a powerful, automatic process, sometimes you need more granular control. Context editing is a set of beta features that allow you to programmatically clear specific parts of the conversation history before it is sent to the model. For example, the clear_tool_uses_20250919 feature allows you to remove the verbose outputs of tool calls once the agent has processed them and extracted the necessary information. This is a lighter-touch form of context management that can significantly reduce token usage without losing the high-level flow of the conversation.

8.3. Session Management as a Workflow Primitive
The SDK treats sessions as first-class citizens, providing a robust set of primitives for managing them. This is the key to building reliable, long-running background agents.

Resumption: Every query is assigned a unique session ID. You can use this ID to resume a session at any time, even if your application has restarted. The agent will pick up exactly where it left off.
Forking: You can “fork” an existing session to create a new, independent branch of the conversation. This is incredibly useful for safe experimentation. You can let an agent try a risky or expensive approach in a forked session, and if it doesn’t work out, you can simply discard the fork and resume the original session, which remains untouched.
Point-in-Time Recovery: The SDK supports resuming a session from a specific message UUID (resumeSessionAt: 'message-uuid') and even rewinding file changes to a previous state (rewindFiles()). This provides an unparalleled level of control and recoverability.
8.4. Beyond the Context Window: The Memory Tool and CLAUDE.md
For true long-term persistence that survives across sessions and even across different agents, the SDK provides two file-based memory mechanisms:

The Memory Tool: This built-in tool allows an agent to read and write to a dedicated /memories directory. The agent can use this to offload critical information, create scratchpads, or maintain a persistent knowledge base that it can refer to in future sessions.
CLAUDE.md: A special file in the project's root or .claude directory that serves as a persistent, high-level instruction set for any agent working in that project. It's the agent equivalent of a team's mission statement or a project's README.md.
8.5. The “Initializer + Coder” Harness Pattern
For very long and complex tasks, such as migrating a large codebase, Anthropic recommends a powerful pattern that combines these memory techniques. The “Initializer + Coder” harness involves two distinct agent phases:

The Initializer Agent: A short-lived agent that runs once at the beginning of the task. Its job is to set up the environment, analyze the problem, and create a detailed plan of action, which it saves to an external artifact like a TODO.md file.
The Coder Agent: A long-running agent that is invoked repeatedly. In each session, it reads the TODO.md file to understand the overall progress, completes the next incremental step in the plan, and then updates the TODO.md file before exiting.
This pattern demonstrates a crucial insight: for truly long-horizon tasks, relying on the context window alone is not enough. The most reliable agents are those that, like human developers, leave behind clear, externally legible artifacts that allow them (or other agents) to rehydrate their state and continue their work, even from a cold start.

Part 9: Production Operations — From Localhost to Scalable Deployments
Building a proof-of-concept agent is one thing; deploying a fleet of reliable, secure, and cost-effective agents in a production environment is another challenge entirely. The Claude Agent SDK is designed with production realities in mind, and the ecosystem provides a clear set of best practices for operations.

9.1. Hosting and Resource Sizing
Anthropic’s official guidance is clear: agents should always be run in sandboxed container environments (e.g., Docker, gVisor, Firecracker). For multi-tenant applications, a new, ephemeral container should be spun up for each user task and destroyed upon completion. This provides the strongest level of isolation for processes, filesystems, and network resources.

In terms of resource allocation, a typical agent container requires:

RAM: ~1 GiB
Disk: ~5 GiB (to accommodate the SDK, dependencies, and working files)
CPU: A baseline of 1 vCPU is usually sufficient, though more may be needed for compute-intensive tool calls.
These lightweight requirements make it feasible to run hundreds or thousands of concurrent agent sessions on a modern cloud infrastructure.

9.2. Observability: Tracing, Logging, and Cost Tracking
Because agents are non-deterministic, robust observability is not a luxury; it is a requirement. You cannot debug what you cannot see. The SDK provides deep, native integrations with leading observability platforms:

LangSmith: A simple configure_claude_agent_sdk() call is all that's needed to get full tracing.
MLflow: mlflow.anthropic.autolog() provides automatic logging of all agent interactions.
OpenTelemetry: The SDK has native support for OTel, allowing you to export traces, logs, and metrics to any compatible backend, including Grafana, Arize, Datadog, Honeycomb, Sentry, and Logfire.
Furthermore, every SDKResultMessage contains rich, structured telemetry, including total_cost_usd, duration_ms, and per-model modelUsage breakdowns. This allows you to build custom dashboards and alerting systems to monitor the cost and performance of your agents in real-time.

9.3. The Usage & Cost Admin API
For organization-wide financial governance, Anthropic provides a Usage & Cost Admin API. This programmatic interface, which requires a special Admin API key, gives you access to historical usage and cost data for your entire organization. You can use it to build sophisticated cost-tracking systems, reconcile billing, and perform advanced analysis that goes beyond what is available in the standard console UI.

9.4. Known Limitations & Production Gotchas
No framework is perfect, and deploying the Agent SDK at scale has revealed several important limitations and “gotchas” that every production engineer should be aware of:

The query() Overhead: The stateless query() function has a significant startup overhead of ~12 seconds due to the time it takes to initialize the underlying Claude Code CLI process. This makes it unsuitable for latency-sensitive applications. The community's #1 requested feature is a "hot process reuse" mode to eliminate this delay.
The allowedTools Bypass Bug: There is a known bug where the allowedTools whitelist can sometimes be bypassed during complex, multi-turn conversations. The recommended workaround is to use a three-layer defense: define your allowedTools, explicitly blacklist everything else in disallowedTools, and implement a canUseTool callback as a final check.
The Context Limit Failure: Once a session’s context limit is reached, all subsequent requests in that session will fail permanently. It is critical to proactively monitor token usage and fork the session to a new one before the limit is hit.
Real-World Data Messiness: The pristine, well-structured data used in testing rarely reflects the messy reality of production. One practitioner documented an agent whose accuracy dropped from a perfect 10/10 in testing to ~60% in production simply due to the unpredictable nature of real-world inputs. This underscores the absolute necessity of building robust evaluation pipelines that test your agents against realistic, diverse, and sometimes malformed data.
Part 10: Real-World Case Studies
Theory and benchmarks are useful, but the true measure of a framework is the tangible value it creates in the real world. The Claude Agent SDK has been adopted by a wide range of companies, from startups to Fortune 500 giants, to automate complex workflows and unlock new capabilities. Here are some of the most impactful, publicly-documented case studies.

Spotify: Automating Codebase Modernization at Scale
Problem: Spotify needed to perform large-scale, repetitive migrations across its massive Java codebase, a task that was consuming significant developer time.

Solution: In collaboration with Anthropic, Spotify used the Agent SDK to build a fleet of background coding agents. These agents are triggered by engineers via a Slack bot. The agent checks out the relevant code, performs the migration (e.g., converting from Java AutoValue to Records), runs the linter, builds the code, executes the test suite, and, if everything passes, automatically opens a pull request.

Impact: This system is now merging over 650 pull requests per month, resulting in a 90% time saving for developers on these tasks. It has become a cornerstone of their developer productivity platform.

BGL Group: Democratizing Business Intelligence
Problem: BGL Group, a leading financial services administration provider in Australia serving over 12,700 businesses, had a wealth of data locked away in over 400 analytics tables. Accessing this data required specialized knowledge, creating a bottleneck for business users.

Solution: Using the Claude Agent SDK on Amazon Bedrock AgentCore, BGL built a natural language business intelligence agent. The agent can understand questions from employees, write and execute the necessary SQL and Python code against the data warehouse in a secure, isolated microVM, and return the answer in plain English.

Impact: Over 200 employees can now get instant answers to complex business questions, democratizing data access and dramatically accelerating decision-making.

Apple: Native Agent Integration in Xcode 26.3
Problem: While IDEs have long had code completion, they have lacked a true understanding of a developer’s intent and the ability to perform complex, multi-file operations autonomously.

Solution: In a landmark announcement in February 2026, Apple revealed that Xcode 26.3 would feature native integration of the Claude Agent SDK. This goes far beyond simple code suggestions. The integration allows the agent to have a deep, project-wide understanding of the code, enabling it to perform tasks like refactoring a class across multiple files, generating a SwiftUI interface with a live visual preview, or ensuring architectural consistency across an entire iOS, macOS, and Vision Pro application.

Impact: This integration, which garnered over 9,500 likes on its announcement post on X, represents a fundamental shift in the developer experience, moving from a human-led process to a human-supervised, agent-driven one.

The Startup Ecosystem: A Cambrian Explosion of Agentic Products
Beyond the enterprise, the Agent SDK has fueled a vibrant ecosystem of startups building innovative products:

Marketing and Content: A demo of an “Ad Agent” that could autonomously research competitors, generate creative copy, and package a report received over 700 likes on X, showcasing the potential for automating creative workflows.
Compliance: A startup idea for a “SOC 2 Compliance Agent” that could automatically gather evidence, review policies, and prepare for audits garnered over 1,000 likes, highlighting the demand for agents in regulated industries.
Research: A research agent was shown to be able to analyze over 200 customer feedback documents in just 12 minutes, a task that would have taken a human over 6 hours.
These examples, from massive enterprises to solo developers, illustrate the profound and versatile impact of the Agent SDK. It is not just a tool for coders; it is a general-purpose framework for building a new class of software that can reason, plan, and act autonomously.

Part 11: The Future — What’s Next for the Agent SDK?
As powerful as the Claude Agent SDK is today, it is still evolving rapidly. By examining Anthropic’s official roadmap signals, the priorities of the open-source community, and the broader strategic vision, we can get a clear picture of where the framework is headed.

11.1. Official Roadmap Signals
Anthropic’s own development priorities point toward a more mature, ergonomic, and powerful agent-building experience:

The TypeScript V2 Preview: The ongoing work on a V2 of the TypeScript SDK, with its cleaner separation of send() and stream() and improved session management patterns, indicates a strong focus on improving the developer experience for complex, interactive applications.
Agent Skills as an Open Standard: Anthropic’s decision to launch Agent Skills as an open standard at agentskills.io, which has already been adopted by Microsoft and Cursor, is a major strategic move. It signals a future where a rich ecosystem of third-party, portable skills can be plugged into any compatible agent, creating a flywheel of innovation.
Research into Long-Horizon Tasks: The company’s published research on multi-context-window workflows and the “initializer + coder” pattern shows a deep and continued investment in solving the hardest problems related to sustained autonomous operation.
11.2. Community Priorities
The open-source community on GitHub has also made its priorities clear. The most highly-requested features and bug fixes provide a ground-level view of what developers need most:

Eliminate Process Initialization Overhead: The #1 request is to implement a “hot process reuse” mode to eliminate the ~12-second startup delay for the query() function, which would make the SDK viable for a much wider range of interactive use cases.
Reliable Permission Enforcement: Fixing the allowedTools bypass bug and generally hardening the permission system is a top priority for developers building production applications.
Full Model Alias Support: Developers want the ability to use all available model aliases (e.g., claude-opus-latest) to ensure their agents are always running on the most current model version without requiring code changes.
11.3. The Grand Vision: A Vertically Integrated Stack
Zooming out, the individual features and roadmap items coalesce into a clear and ambitious grand vision. Anthropic is not just building a library; it is building a complete, vertically integrated stack for the agentic era:

The Protocol Layer: The Model Context Protocol (MCP) provides the universal language for agents to communicate with tools.
The Capability Layer: Agent Skills provide a portable, reusable format for encapsulating knowledge and workflows.
The Runtime Layer: The Claude Agent SDK provides the secure, robust, and battle-tested runtime for executing agentic logic.
This integrated stack, powered by Anthropic’s state-of-the-art models, represents a powerful bet on the future of software development. It is a future where the primary job of a developer is not just to write code, but to orchestrate intelligence.

Part 12: CI/CD Integration — GitHub Actions and Automation Pipelines
One of the most immediately practical applications of the Claude Agent SDK is in CI/CD pipelines. The official Claude Code GitHub Actions are built directly on top of the Agent SDK, making it the underlying runtime for automated code review, testing, and deployment workflows.

The Official GitHub Actions
Anthropic provides pre-built GitHub Actions that can be dropped into any repository’s workflow:

Example: Automated Code Review on Pull Requests

name: Claude Code Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: |
            Review this pull request for:
            1. Security vulnerabilities
            2. Performance issues
            3. Code style violations
            4. Missing test coverage
            Provide specific, actionable feedback as PR comments.
          model: claude-sonnet-4-5
          max_budget_usd: "0.50"
          allowed_tools: "Read,Grep,Glob,Bash(npm test *)"
Example: Automated Issue Resolution

name: Auto-fix Issues
on:
  issues:
    types: [labeled]

jobs:
  fix:
    if: github.event.label.name == 'claude-fix'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          prompt: |
            Read the issue description and fix the reported bug.
            Create a new branch, make the fix, run the tests,
            and open a pull request with a clear description.
          permission_mode: acceptEdits
          max_turns: 30
These actions demonstrate the SDK’s power in headless, fully-automated environments. By setting permission_mode to acceptEdits or bypassPermissions (with sandboxing), you can create agents that autonomously fix bugs, update documentation, and manage releases without any human intervention.

Part 13: Structured Outputs and Output Formatting
For many production use cases, you need the agent’s final output in a specific, machine-readable format rather than free-form text. The SDK provides two powerful mechanisms for this.

JSON Schema Enforcement
The output_format option accepts a JSON schema that forces the agent's final response to conform to a specific structure. This is invaluable for building agents that feed their results into downstream systems.

options = ClaudeAgentOptions(
    output_format={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "A one-paragraph summary of the analysis"},
            "risk_level": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                        "issue": {"type": "string"},
                        "recommendation": {"type": "string"}
                    }
                }
            }
        },
        "required": ["summary", "risk_level", "findings"]
    }
)
If the agent fails to produce valid output after multiple attempts, the SDKResultMessage will have a subtype of error_max_structured_output_retries, allowing you to handle the failure gracefully.

System Prompt Presets and Output Styles
The SDK also provides four methods for setting the system prompt, which directly influences the agent’s output style:

Custom string: A fully custom system prompt.
Preset with append (recommended): Uses the battle-tested Claude Code system prompt as a base and appends your custom instructions.
CLAUDE.md files: Project-level instructions loaded from the filesystem.
Output styles: Predefined formatting instructions (e.g., concise, verbose, academic).
Part 14: Conclusion — The New Stack for AI Engineering
We are at a pivotal moment in the history of software. The transition from conversational AI to autonomous agents is as fundamental as the shift from mainframes to personal computers, or from desktop applications to the web. It demands a new generation of tools, a new set of architectural patterns, and a new way of thinking about the role of the developer.

The Claude Agent SDK stands at the forefront of this transition. It is more than just a library; it is a comprehensive, production-grade framework that provides the essential building blocks for this new era. By offering a battle-tested agent loop, a rich set of built-in tools, a sophisticated multi-layered security model, and a clear path to extensibility, it dramatically lowers the barrier to building powerful, autonomous agents that can create real-world value.

The journey of the SDK itself — from a tool for extending a coding assistant to a general-purpose agent framework — mirrors the journey that our entire industry is on. The era of agent engineering is here, and the defining challenges have shifted. The new frontier is not just about model selection; it is about workflow design, governance, observability, and, most importantly, trust.

As building agents becomes easier, the defining challenge will be building agents we can trust. The future of software engineering is not just about writing code; it is about orchestrating intelligence. The Claude Agent SDK provides a powerful, and perhaps essential, foundation for that future.

References
[1] Anthropic. (2026, February 5). Claude Opus 4.6 Release Notes. https://www.anthropic.com/news/claude-opus-4-6

[2] Spotify Engineering. (2025, November 18). Background Coding Agents: Context Engineering (Part 2). https://engineering.atspotify.com/2025/11/context-engineering-background-coding-agents-part-2

[3] Anthropic. (2026, February 12). Apple’s Xcode now supports the Claude Agent SDK. https://www.anthropic.com/news/apple-xcode-claude-agent-sdk

[4] AWS Machine Learning Blog. (2026, January 22). Democratizing business intelligence: BGL’s journey with Claude Agent SDK and Amazon Bedrock AgentCore. https://aws.amazon.com/blogs/machine-learning/democratizing-business-intelligence-bgls-journey-with-claude-agent-sdk-and-amazon-bedrock-agentcore

[5] Anthropic. (2025, September 29). Claude Agent SDK Documentation. https://platform.claude.com/docs/en/agent-sdk

[6] AgencyAI. (2026, January 15). 20+ Real Use Cases That Prove Claude Code Is a Game-Changer. https://medium.com/@agencyai/20-real-use-cases-that-prove-claude-code-is-a-game-changer-46ceefaf19ed

[7] Post by @boringmarketer on X. (2026, February 1). https://x.com/boringmarketer/status/2008607337532014709

[8] Post by @gregisenberg on X. (2026, January 20). https://x.com/gregisenberg/status/1997425488063525050

[9] AgentSkills.io. (2025, December 18). The Agent Skills Open Standard. https://agentskills.io

[10] GitHub. anthropic/claude-agent-sdk-demos. https://github.com/anthropics/claude-agent-sdk-demos

Artificial Intelligence
LLM
Agents
Claude
AI Agent
1


1


Bragi
Written by Bragi
31 followers
·
0 following

Follow
Responses (1)

Write a response

What are your thoughts?

Cancel
Respond
Vivek Raja
Vivek Raja

5 days ago


This is a great getting started guide!
We found the Claude Agent SDK difficult to deploy, so we built a solution that makes it easy: https://www.terminaluse.com/
The cool thing is that the platform is CLI-first. This makes it easy for Claude Code / Codex to help you engineer an agent!

1 reply

Reply

More from Bragi
Unveiling the Challenges of Transformers in Time Series Forecasting: Are GBDTs Still the Gold…
Bragi
Bragi

Unveiling the Challenges of Transformers in Time Series Forecasting: Are GBDTs Still the Gold…
In the realm of machine learning and artificial intelligence, the buzz around transformers and their applicability across various domains…
Mar 9, 2024
113
1
The Power of Model Merging: Creating State-of-the-Art Language Models on a Budget
Bragi
Bragi

The Power of Model Merging: Creating State-of-the-Art Language Models on a Budget
Developing massive language models with trillions of parameters, has led to remarkable breakthroughs in natural language processing. But…
May 10, 2024
15
Beyond Basic Rewards: Unlocking Advanced Reasoning in AI with GRPO
Bragi
Bragi

Beyond Basic Rewards: Unlocking Advanced Reasoning in AI with GRPO
In recent years, the artificial intelligence community has witnessed a dramatic evolution in how large language models (LLMs) generate and…
Feb 20, 2025
10
See all from Bragi
Recommended from Medium
Claude Code + MiniMax M2.5
Joe Njenga
Joe Njenga

I Tested Claude Code + MiniMax M2.5 (It Blew My Mind With One Shot CLI Tool Build )
Just when I thought GLM 5 was the best budget model for Claude Code, MiniMax M2.5 showed up and changed my mind.

5d ago
151
4
Designing efficient Agentic AI Workflows
AI Advances
In

AI Advances

by

Debmalya Biswas

Designing efficient Agentic AI Workflows
Agentification UI/UX: Mapping Enterprise Processes to Agentic Execution Graphs

Feb 8
305
7
10 OpenClaw Use Cases for a Personal AI Assistant
Balazs Kocsis
Balazs Kocsis

10 OpenClaw Use Cases for a Personal AI Assistant
How are people actually using OpenClaw, and how are they integrating it?

Jan 27
149
4
I Cancelled My ~$200/mo Claude API Subscription, Again.
Towards AI
In

Towards AI

by

Adham Khaled

I Cancelled My ~$200/mo Claude API Subscription, Again.
Kimi K2.5 didn’t just lower the price. It destroyed the business model of “renting intelligence.”

Feb 8
1.6K
48
Finally, A Native Agentic Framework for SLMs
Pawel
Pawel

Finally, A Native Agentic Framework for SLMs
I’ve been looking for this for a while — an agentic framework that doesn’t assume you’re running proprietary LLMs through an API.

Feb 7
167
3
OpenClaw Security: My Complete Hardening Guide for VPS and Docker Deployments
Reza Rezvani
Reza Rezvani

OpenClaw Security: My Complete Hardening Guide for VPS and Docker Deployments
A practical guide to securing your AI assistant — from first install to production-ready deployment

Feb 2
