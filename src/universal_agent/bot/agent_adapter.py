import os
from .execution_logger import ExecutionLogger

class AgentAdapter:
    def __init__(self):
        self.initialized = False
        self.options = None
        self.session = None
        self.user_id = None
        self.workspace_dir = None
        self.trace = None

    async def initialize(self):
        """Initialize a fresh agent session."""
        from universal_agent.main import setup_session
        
        print("🤖 Initializing Agent Session...")
        self.options, self.session, self.user_id, self.workspace_dir, self.trace = await setup_session()
        self.initialized = True
        print(f"🤖 Agent Session Initialized. Workspace: {self.workspace_dir}")

    async def reinitialize(self):
        """Force a fresh session, destroying any existing one."""
        print("🔄 Reinitializing Agent Session (fresh start)...")
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
                              If True, reuse existing session for multi-turn.
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

        from universal_agent.main import process_turn, ClaudeSDKClient
        
        # Use ExecutionLogger to capture stdout/stderr for this specific task
        with ExecutionLogger(log_file_path):
            print(f"=== Task Execution Start: {task.id} ===")
            print(f"User ID: {task.user_id}")
            print(f"Prompt: {task.prompt}")
            print(f"Continue Session: {continue_session}")
            print("-" * 50)
            
            try:
                # Re-create client for each task to ensure clean state
                async with ClaudeSDKClient(self.options) as client:
                    response_text = await process_turn(client, task.prompt, self.workspace_dir)
                    
                    if not response_text:
                        response_text = "(No text response returned by agent)"
                        
                    task.result = response_text
                    
            except Exception as e:
                print(f"🔥 Critical Error during execution: {e}")
                import traceback
                traceback.print_exc()
                raise e
            
            print("-" * 50)
            print(f"=== Task Execution End: {task.id} ===")
