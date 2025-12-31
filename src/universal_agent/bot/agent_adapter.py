import os
import sys
from .execution_logger import ExecutionLogger

class AgentAdapter:
    def __init__(self):
        self.initialized = False
        self.options = None
        self.session = None
        self.user_id = None
        self.workspace_dir = None
        self.trace = None
        
        # Persistent Client State
        self.client = None
        self._client_context = None

    async def initialize(self):
        """Initialize a fresh agent session and client."""
        from universal_agent.main import setup_session, ClaudeSDKClient
        
        print("🤖 Initializing Agent Session...")
        self.options, self.session, self.user_id, self.workspace_dir, self.trace = await setup_session()
        
        # Create persistent client
        self.client = ClaudeSDKClient(self.options)
        self._client_context = await self.client.__aenter__()
        
        self.initialized = True
        print(f"🤖 Agent Session Initialized. Workspace: {self.workspace_dir}")

    async def reinitialize(self):
        """Force a fresh session, destroying any existing one."""
        print("🔄 Reinitializing Agent Session (fresh start)...")
        
        # Cleanup old client
        if self._client_context:
            await self.client.__aexit__(None, None, None)
            self._client_context = None
            self.client = None
            
        self.initialized = False
        self.options = None
        self.session = None
        self.user_id = None
        self.workspace_dir = None
        self.trace = None
        await self.initialize()

    async def execute(self, task, continue_session: bool = False):
        """
        Executes a task using the agent.
        
        Args:
            task: The task to execute
            continue_session: If False (default), reinitialize to fresh session.
                              If True, reuse existing session AND client for multi-turn.
        """
        # Session management: fresh by default, continue if requested
        if not continue_session or not self.initialized:
            await self.reinitialize()
        else:
            print("🔗 Continuing in existing session...")

        # Setup Logging
        log_file_name = f"task_{task.id}.log"
        log_file_path = os.path.join(self.workspace_dir, log_file_name)
        task.log_file = log_file_path
        task.workspace = self.workspace_dir

        from universal_agent.main import process_turn
        
        # Use ExecutionLogger to capture stdout/stderr for this specific task
        with ExecutionLogger(log_file_path):
            print(f"=== Task Execution Start: {task.id} ===")
            print(f"User ID: {task.user_id}")
            print(f"Prompt: {task.prompt}")
            print(f"Continue Session: {continue_session}")
            print("-" * 50)
            
            try:
                # Use persistent client (setup in initialize/reinitialize)
                if not self.client:
                    raise RuntimeError("Agent Client is not initialized!")
                    
                result = await process_turn(self.client, task.prompt, self.workspace_dir)
                
                # Store rich execution data
                task.execution_summary = result
                task.result = result.response_text
                
                if not task.result:
                    task.result = "(No text response returned by agent)"
                    
            except Exception as e:
                print(f"🔥 Critical Error during execution: {e}")
                import traceback
                traceback.print_exc()
                
                # If client crashed, force reinit next time
                self.initialized = False
                task.result = f"Error: {e}"
                task.status = "error" # Ensure error status propagates
                # Don't re-raise, let task manager handle it via status
            
            print("-" * 50)
            print(f"=== Task Execution End: {task.id} ===")
            
    async def shutdown(self):
        """Cleanup resources on shutdown."""
        if self._client_context:
            await self.client.__aexit__(None, None, None)
            self._client_context = None
            self.client = None
