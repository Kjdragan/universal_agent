"""
Universal Ralph Wrapper - Integration Module

This module provides example implementations for integrating URW with
various agent frameworks. The key interface is AgentLoopInterface,
which your existing multi-agent system must implement.

CRITICAL: Each call to execute_task() MUST use a fresh agent instance
with a clean context window. The only context the agent receives is
what's explicitly passed in the `context` parameter.
"""

import asyncio
import time
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

from urw_state import Task
from urw_orchestrator import AgentLoopInterface, AgentExecutionResult


# =============================================================================
# BASE ADAPTER CLASS
# =============================================================================

class BaseAgentAdapter(AgentLoopInterface):
    """
    Base class for agent loop adapters.
    
    Subclass this and implement _create_agent() and _run_agent() to
    integrate your specific multi-agent system.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: Configuration for your agent system (API keys, model, etc.)
        """
        self.config = config
        self._current_agent = None
        self._cancelled = False
    
    @abstractmethod
    async def _create_agent(self) -> Any:
        """
        Create a fresh agent instance.
        
        MUST return a new agent with clean context window.
        Do NOT reuse agent instances between calls.
        """
        pass
    
    @abstractmethod
    async def _run_agent(self, agent: Any, prompt: str, 
                        workspace_path: Path) -> AgentExecutionResult:
        """
        Run the agent on the given prompt.
        
        Args:
            agent: Fresh agent instance from _create_agent()
            prompt: Full prompt including task and context
            workspace_path: Path to workspace for file operations
        
        Returns:
            AgentExecutionResult with execution details
        """
        pass
    
    async def execute_task(self, task: Task, context: str,
                          workspace_path: Path) -> AgentExecutionResult:
        """
        Execute a task with the provided context.
        
        This is the main entry point called by URW.
        """
        self._cancelled = False
        
        # Build the full prompt
        prompt = self._build_prompt(task, context)
        
        # Create fresh agent
        agent = await self._create_agent()
        self._current_agent = agent
        
        try:
            # Run agent
            start_time = time.time()
            result = await self._run_agent(agent, prompt, workspace_path)
            result.execution_time_seconds = time.time() - start_time
            return result
        finally:
            # Ensure agent is cleaned up
            self._current_agent = None
    
    async def cancel(self):
        """Cancel any running execution."""
        self._cancelled = True
        # Subclasses can override to perform additional cleanup
    
    def _build_prompt(self, task: Task, context: str) -> str:
        """Build the full prompt for the agent."""
        return f"""# Universal Ralph Wrapper Task

{context}

---

## Instructions

You are executing a task as part of a larger plan. Your context window is fresh - 
all relevant history is provided above in the context section.

**Your Task:** {task.title}

{task.description}

**Success Criteria:**
{self._format_criteria(task)}

## Guidelines

1. Work towards completing the task fully
2. Save all outputs to the workspace artifacts directory
3. If you encounter blockers, document them clearly
4. Extract learnings that might help future iterations
5. If an approach fails, note it so it won't be repeated

## Output Format

When complete, provide a summary of:
- What was accomplished
- Files created (if any)
- Any actions taken (emails sent, APIs called, etc.)
- Key learnings
- Any approaches that failed
"""
    
    def _format_criteria(self, task: Task) -> str:
        """Format task criteria for the prompt."""
        criteria = []
        
        if task.binary_checks:
            for check in task.binary_checks:
                criteria.append(f"- {check}")
        
        if task.constraints:
            for constraint in task.constraints:
                criteria.append(f"- {constraint['type']}: {constraint.get('value', '')}")
        
        if task.evaluation_rubric:
            criteria.append(f"- Qualitative: {task.evaluation_rubric}")
        
        return '\n'.join(criteria) if criteria else "- Complete the task as described"


# =============================================================================
# CLAUDE AGENT SDK ADAPTER
# =============================================================================

class ClaudeAgentSDKAdapter(BaseAgentAdapter):
    """
    Adapter for the Claude Agent SDK (formerly Claude Code SDK).
    
    This creates a fresh Agent instance for each task execution,
    ensuring clean context windows.
    
    Requirements:
        pip install claude-agent-sdk
    
    Config options:
        - api_key: Anthropic API key
        - model: Model to use (default: claude-sonnet-4-20250514)
        - tools: List of tool configurations
        - max_turns: Maximum conversation turns (default: 50)
    """
    
    async def _create_agent(self) -> Any:
        """Create a fresh Claude Agent SDK instance."""
        
        # Import here to avoid hard dependency
        try:
            from claude_agent_sdk import Agent
        except ImportError:
            raise ImportError(
                "claude-agent-sdk not installed. "
                "Install with: pip install claude-agent-sdk"
            )
        
        # Create fresh agent instance
        agent = Agent(
            model=self.config.get('model', 'claude-sonnet-4-20250514'),
            api_key=self.config.get('api_key'),
            tools=self.config.get('tools', []),
            system_prompt=self.config.get('system_prompt', ''),
        )
        
        return agent
    
    async def _run_agent(self, agent: Any, prompt: str,
                        workspace_path: Path) -> AgentExecutionResult:
        """Run the Claude Agent SDK on the prompt."""
        
        artifacts = []
        side_effects = []
        learnings = []
        failed_approaches = []
        tools_invoked = []
        
        try:
            # Run the agent
            result = await agent.run(
                prompt=prompt,
                max_turns=self.config.get('max_turns', 50),
            )
            
            # Extract results
            output = result.final_response if hasattr(result, 'final_response') else str(result)
            
            # Parse tool usage from result
            if hasattr(result, 'tool_calls'):
                tools_invoked = [tc.name for tc in result.tool_calls]
            
            # Estimate token usage
            context_tokens = len(prompt.split()) * 1.3  # Rough estimate
            
            return AgentExecutionResult(
                success=True,
                output=output,
                artifacts_produced=artifacts,
                side_effects=side_effects,
                learnings=learnings,
                failed_approaches=failed_approaches,
                tools_invoked=tools_invoked,
                context_tokens_used=int(context_tokens),
            )
            
        except Exception as e:
            return AgentExecutionResult(
                success=False,
                output="",
                error=str(e),
            )


# =============================================================================
# UNIVERSAL AGENT ADAPTER (Your System)
# =============================================================================

class UniversalAgentAdapter(BaseAgentAdapter):
    """
    Adapter for Kevin's Universal Agent system.
    
    This integrates with the existing multi-agent architecture:
    - Primary Agent (Claude Sonnet 4)
    - Sub-agents spawned as needed
    - Composio Tool Router for 500+ tools
    - Letta Memory Blocks for in-session context
    
    Config options:
        - api_key: Anthropic API key
        - composio_api_key: Composio API key
        - composio_user_id: Composio user ID
        - model: Model to use (default: claude-sonnet-4-20250514)
        - tools: Additional tool configurations
    """
    
    async def _create_agent(self) -> Any:
        """
        Create a fresh Universal Agent instance.
        
        This should instantiate your existing agent system
        with a clean context window.
        """
        # Import your agent system here
        # This is a placeholder - replace with your actual imports
        
        # Example structure based on your repo:
        # from universal_agent.main import create_agent
        # from universal_agent.bot.agent_adapter import AgentAdapter
        
        config = {
            'model': self.config.get('model', 'claude-sonnet-4-20250514'),
            'api_key': self.config.get('api_key'),
            'composio_api_key': self.config.get('composio_api_key'),
            'composio_user_id': self.config.get('composio_user_id'),
        }
        
        # Return a dict representing the agent config
        # Your actual implementation would create the real agent
        return config
    
    async def _run_agent(self, agent: Any, prompt: str,
                        workspace_path: Path) -> AgentExecutionResult:
        """
        Run the Universal Agent on the prompt.
        
        This should call your existing agent run method.
        """
        # Placeholder implementation
        # Replace with calls to your actual agent system
        
        # Example:
        # from universal_agent.main import run_agent
        # result = await run_agent(agent, prompt)
        
        # For now, return a placeholder result
        return AgentExecutionResult(
            success=True,
            output="[Placeholder] Implement _run_agent with your actual agent system",
            artifacts_produced=[],
            side_effects=[],
            learnings=["Integration pending"],
            failed_approaches=[],
            tools_invoked=[],
            context_tokens_used=0,
        )


# =============================================================================
# MOCK ADAPTER FOR TESTING
# =============================================================================

class MockAgentAdapter(BaseAgentAdapter):
    """
    Mock adapter for testing URW without a real agent system.
    
    Simulates agent execution with configurable behavior.
    
    Config options:
        - success_rate: Probability of task success (0-1)
        - simulate_delay: Seconds to delay (simulates work)
        - produce_artifacts: Whether to create mock artifacts
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.execution_count = 0
    
    async def _create_agent(self) -> Any:
        """Create a mock agent."""
        return {"mock": True, "execution": self.execution_count}
    
    async def _run_agent(self, agent: Any, prompt: str,
                        workspace_path: Path) -> AgentExecutionResult:
        """Simulate agent execution."""
        import random
        
        self.execution_count += 1
        
        # Simulate work
        delay = self.config.get('simulate_delay', 0.5)
        await asyncio.sleep(delay)
        
        # Determine success
        success_rate = self.config.get('success_rate', 0.8)
        success = random.random() < success_rate
        
        # Create mock artifacts if configured
        artifacts = []
        if self.config.get('produce_artifacts', True) and success:
            artifact_path = f"mock_output_{self.execution_count}.md"
            full_path = workspace_path / '.urw' / 'artifacts' / artifact_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(f"# Mock Output\n\nExecution {self.execution_count}\n\n{prompt[:500]}")
            artifacts.append({
                "path": artifact_path,
                "type": "file",
                "metadata": {"mock": True}
            })
        
        # Generate mock learnings
        learnings = [
            f"Mock learning from execution {self.execution_count}",
        ]
        
        # Maybe generate failed approaches
        failed_approaches = []
        if not success:
            failed_approaches.append({
                "approach": f"Mock approach {self.execution_count}",
                "why_failed": "Simulated failure for testing"
            })
        
        return AgentExecutionResult(
            success=success,
            output=f"Mock execution {self.execution_count} completed. Success: {success}",
            error=None if success else "Simulated failure",
            artifacts_produced=artifacts,
            side_effects=[],
            learnings=learnings,
            failed_approaches=failed_approaches,
            tools_invoked=["mock_tool_1", "mock_tool_2"],
            context_tokens_used=len(prompt.split()) * 2,
        )


# =============================================================================
# INTEGRATION HELPERS
# =============================================================================

def create_adapter_for_system(system_type: str, config: Dict) -> BaseAgentAdapter:
    """
    Factory function to create the appropriate adapter.
    
    Args:
        system_type: One of 'claude_sdk', 'universal_agent', 'mock'
        config: Configuration dict for the adapter
    
    Returns:
        Configured adapter instance
    """
    adapters = {
        'claude_sdk': ClaudeAgentSDKAdapter,
        'universal_agent': UniversalAgentAdapter,
        'mock': MockAgentAdapter,
    }
    
    adapter_class = adapters.get(system_type)
    if not adapter_class:
        raise ValueError(f"Unknown system type: {system_type}. "
                        f"Options: {list(adapters.keys())}")
    
    return adapter_class(config)


# =============================================================================
# INTEGRATION GUIDE
# =============================================================================

"""
## Integration Guide for Your Multi-Agent System

### Step 1: Implement the Adapter

Create a new adapter class that inherits from BaseAgentAdapter:

```python
class YourAgentAdapter(BaseAgentAdapter):
    async def _create_agent(self) -> Any:
        # Import your agent system
        from your_system import Agent
        
        # Create FRESH instance - do NOT reuse
        return Agent(
            api_key=self.config['api_key'],
            # ... other config
        )
    
    async def _run_agent(self, agent, prompt, workspace_path):
        # Run your agent
        result = await agent.run(prompt)
        
        # Convert to AgentExecutionResult
        return AgentExecutionResult(
            success=result.success,
            output=result.output,
            artifacts_produced=[...],  # Extract from result
            # ... etc
        )
```

### Step 2: Configure and Run

```python
from urw_package import URWOrchestrator, URWConfig

# Your adapter
adapter = YourAgentAdapter({
    'api_key': 'sk-...',
    # ... other config
})

# Create orchestrator
orchestrator = URWOrchestrator(
    agent_loop=adapter,
    llm_client=anthropic_client,
    workspace_path=Path('./workspace'),
    config=URWConfig(
        max_iterations_per_task=15,
        verbose=True,
    )
)

# Run a task
result = await orchestrator.run("Research quantum computing and write a report")
```

### Critical Requirements

1. **Fresh Context Every Call**
   - _create_agent() MUST return a new instance
   - Do NOT reuse agent objects between execute_task() calls
   - The agent should have NO memory of previous calls

2. **Context is Injected via Prompt**
   - The `context` parameter contains all relevant history
   - Your agent should NOT have its own persistence
   - URW handles all state management

3. **Extract Structured Results**
   - Parse your agent's output to identify:
     - Artifacts created (files)
     - Side effects (emails sent, API calls)
     - Learnings (insights for future iterations)
     - Failed approaches (what didn't work)

4. **Handle Cancellation**
   - Implement cancel() to stop long-running executions
   - Check self._cancelled periodically in _run_agent()

### Example: Integrating with Actor Model

If your system uses an Actor pattern:

```python
class ActorAgentAdapter(BaseAgentAdapter):
    async def _create_agent(self):
        # Create a new actor - NOT reuse existing
        return await create_actor(
            config=self.config,
            initial_state={}  # Fresh state
        )
    
    async def _run_agent(self, actor, prompt, workspace_path):
        # Send task to actor
        result = await actor.ask({"type": "execute", "prompt": prompt})
        
        # Actor should terminate after - do NOT keep alive
        await actor.stop()
        
        return AgentExecutionResult(...)
```

### Sub-Agent Handling

Your primary agent can spawn sub-agents WITHIN a single execute_task() call.
This is fine - they share the same context window.

What you CANNOT do:
- Keep sub-agents alive between execute_task() calls
- Pass state from previous sub-agents to new ones

The URW state manager handles all persistence. Your agent system just
executes atomically within each iteration.
"""
