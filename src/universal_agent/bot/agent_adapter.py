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
        if self.initialized:
            return
        
        from universal_agent.main import setup_session
        
        print("🤖 Initializing Agent Session...")
        # This sets up globals in main.py too
        self.options, self.session, self.user_id, self.workspace_dir, self.trace = await setup_session()
        self.initialized = True
        print(f"🤖 Agent Session Initialized. Workspace: {self.workspace_dir}")

    async def execute(self, task):
        """
        Executes a task using the agent.
        """
        if not self.initialized:
            await self.initialize()

        # Setup Logging
        # We save separate logs for each task
        log_file_name = f"task_{task.id}.log"
        log_file_path = os.path.join(self.workspace_dir, log_file_name)
        task.log_file = log_file_path
        task.workspace = self.workspace_dir

        from universal_agent.main import process_turn, ClaudeSDKClient
        
        # Use ExecutionLogger to capture stdout/stderr for this specific task
        # Note: Since MAX_CONCURRENT_TASKS=1, this global redirection is safe.
        # If we go parallel, we need a better logging strategy (e.g. context-local logs).
        with ExecutionLogger(log_file_path):
            print(f"=== Task Execution Start: {task.id} ===")
            print(f"User ID: {task.user_id}")
            print(f"Prompt: {task.prompt}")
            print("-" * 50)
            
            try:
                # Re-create client for each task to ensure clean state
                async with ClaudeSDKClient(self.options) as client:
                    # process_turn returns (response_text)
                    # It also prints execution summary to stdout (captured by logger)
                    response_text = await process_turn(client, task.prompt, self.workspace_dir)
                    
                    if not response_text:
                        response_text = "(No text response returned by agent)"
                        
                    task.result = response_text
                    
            except Exception as e:
                print(f"🔥 Critical Error during execution: {e}")
                import traceback
                traceback.print_exc()
                raise e # Propagate to TaskManager
            
            print("-" * 50)
            print(f"=== Task Execution End: {task.id} ===")
