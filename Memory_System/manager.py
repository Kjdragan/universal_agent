from typing import List, Dict, Any, Optional
from datetime import datetime
import json
import os
from .models import MemoryBlock, ArchivalItem, AgentState
from .storage import StorageManager
from .watcher import MemoryWatcher
from universal_agent.feature_flags import memory_index_enabled

class MemoryManager:
    """
    The High-Level Controller for Agent Memory.
    Manages the 'Brain' of the agent.
    """
    
    def __init__(self, storage_dir: str = "Memory_System/data", workspace_dir: str = None):
        self.storage = StorageManager(storage_dir)
        self.agent_state = self._load_or_initialize_state()
        
        # Determine paths for watcher
        # If workspace_dir is provided, we watch workspace_dir/memory
        # Otherwise we assume default relative path
        self.watch_dir = None
        if workspace_dir:
            self.watch_dir = os.path.join(workspace_dir, "memory")
        elif os.path.exists("memory"):
            self.watch_dir = os.path.abspath("memory")
            
        # Initialize Watcher if enabled
        # Index enabled flag controls "Active" features like watcher
        self.watcher = None
        if memory_index_enabled() and self.watch_dir:
            self.watcher = MemoryWatcher(self.watch_dir, self._on_file_change)
            self.watcher.start()
            
    def _on_file_change(self, filepath: str, content: str):
        """Callback for file watcher."""
        # Auto-index changed files
        filename = os.path.basename(filepath)
        print(f"[MemoryManager] Auto-indexing changed file: {filename}")
        self.archival_memory_insert(content, tags=f"file:{filename},auto-sync")
        
    def close(self):
        """Cleanup resources."""
        if self.watcher:
            self.watcher.stop()

    def _load_or_initialize_state(self) -> AgentState:
        """
        Load agent state from storage, or initialize defaults if empty.
        Mirroring Letta, we allow for multiple Core Memory blocks.
        """
        blocks = self.storage.get_core_memory()
        
        # If no memory exists, seed with Letta-style defaults
        if not blocks:
            # 1. PERSONA: Who the agent is
            persona = MemoryBlock(
                label="persona",
                value=(
                    "I are Antigravity, a powerful agentic AI coding assistant.\n"
                    "I am pair programming with the USER to solve their coding task.\n"
                    "I have access to a persistent memory system."
                ),
                description="The agent's personality and identity."
            )
            
            # 2. HUMAN: Facts about the user
            human = MemoryBlock(
                label="human",
                value=(
                    "Name: User\n"
                    "Preferences: None recorded yet."
                ),
                description="Personal facts about the user (name, location, likes)."
            )
            
            # 3. SYSTEM_RULES: Technical constraints (The 'uv' example goes here)
            system_rules = MemoryBlock(
                label="system_rules",
                value=(
                    "Package Manager: uv (Always use `uv add`)\n"
                    "OS: Linux"
                ),
                description="Technical rules and project constraints."
            )
            
            self.storage.save_block(persona)
            self.storage.save_block(human)
            self.storage.save_block(system_rules)
            blocks = [persona, human, system_rules]
            
        return AgentState(core_memory=blocks)

    def get_system_prompt_addition(self) -> str:
        """
        Format the Core Memory blocks for injection into the System Prompt.
        This provides the 'Context Link' that makes the agent stateful.
        """
        prompt_lines = ["\n# 🧠 CORE MEMORY (Always Available)"]
        
        for block in self.agent_state.core_memory:
            prompt_lines.append(f"\n## [{block.label.upper()}]")
            prompt_lines.append(f"{block.value}")
            
        prompt_lines.append("\nNote: You can update these memory blocks using the `core_memory_replace` tool.")
        prompt_lines.append("Use `archival_memory_insert` to save huge facts/docs that don't fit here.\n")
        
        return "\n".join(prompt_lines)

    # --- Programmatic Accessors ---
    
    def get_memory_block(self, label: str) -> Optional[MemoryBlock]:
        """Direct access to memory block by label."""
        return next((b for b in self.agent_state.core_memory if b.label == label), None)

    def update_memory_block(self, label: str, new_value: str) -> None:
        """Direct update of memory block."""
        block = self.get_memory_block(label)
        if block:
            block.value = new_value
            block.last_updated = datetime.now()
            self.storage.save_block(block)
        else:
            # Create if not exists (Auto-create behavior for new system blocks)
            # This is useful for AGENT_COLLEGE_NOTES if initialized late
            new_block = MemoryBlock(label=label, value=new_value, description="Auto-created block")
            self.agent_state.core_memory.append(new_block)
            self.storage.save_block(new_block)

    # --- Tool Implementations (Bound to this Manager) ---
    
    def mark_trace_processed(self, trace_id: str):
        """Mark a trace ID as processed in persistent storage."""
        self.storage.add_processed_trace(trace_id)

    def has_trace_been_processed(self, trace_id: str) -> bool:
        """Check if a trace ID has been processed."""
        return self.storage.is_trace_processed(trace_id)

    def core_memory_replace(self, label: str, new_value: str) -> str:
        """Tool: Overwrite a specific memory block."""
        # Find block
        block = next((b for b in self.agent_state.core_memory if b.label == label), None)
        if not block:
            return f"Error: Memory block '{label}' not found. Available: {[b.label for b in self.agent_state.core_memory]}"
        
        if not block.is_editable:
             return f"Error: Memory block '{label}' is read-only."
             
        # Update
        old_value = block.value
        block.value = new_value
        block.last_updated = datetime.now()
        
        # Persist
        self.storage.save_block(block)
        
        return f"✅ Successfully updated '{label}' block.\nOld: {old_value[:50]}...\nNew: {new_value[:50]}..."

    def core_memory_append(self, label: str, text_to_append: str) -> str:
        """Tool: Append to a memory block (safer than replace)."""
        block = next((b for b in self.agent_state.core_memory if b.label == label), None)
        if not block:
            return f"Error: Memory block '{label}' not found."
            
        new_val = block.value + "\n" + text_to_append
        return self.core_memory_replace(label, new_val)

    def archival_memory_insert(self, content: str, tags: str = "") -> str:
        """Tool: Save a fact or passage to long-term storage."""
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        
        item = ArchivalItem(content=content, tags=tag_list)
        item_id = self.storage.insert_archival(item)
        
        return f"✅ Saved to Archival Memory (ID: {item_id})"

    def transcript_index(self, session_id: str, content: str):
        """
        Index a transcript chunk.
        ON BY DEFAULT (opt-out implementation would be at caller level).
        """
        # We process it as a normal archival item but tag it specifically
        self.archival_memory_insert(content, tags=f"transcript,session:{session_id}")

    def archival_memory_search(self, query: str, limit: int = 5) -> str:
        """Tool: Semantic search for facts."""
        # Use Hybrid Search
        results = self.storage.search_archival_hybrid(query, limit)
        
        if not results:
            return "No relevant memories found."
            
        output = [f"Found {len(results)} results for '{query}':"]
        for i, item in enumerate(results):
            output.append(f"\n[{i+1}] (Tags: {item.tags})")
            output.append(f"{item.content}")
            
        return "\n".join(output)

    def get_tools_definitions(self) -> List[Dict]:
        """
        Return standard tool definitions for Claude/Composio.
        Using simple functional schema.
        """
        return [
             {
                "name": "core_memory_replace",
                "description": "Overwrite a Core Memory block (e.g. 'human', 'persona'). Use this to update facts about the user or yourself.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "The label of the block to update (e.g. 'human')"},
                        "new_value": {"type": "string", "description": "The new content for the block."}
                    },
                    "required": ["label", "new_value"]
                }
            },
            {
                "name": "core_memory_append",
                "description": "Append text to a Core Memory block. Useful for adding a new preference without deleting old ones.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "description": "The label of the block (e.g. 'human')"},
                        "text_to_append": {"type": "string", "description": "Text to add to the end of the block."}
                    },
                    "required": ["label", "text_to_append"]
                }
            },
            {
                "name": "archival_memory_insert",
                "description": "Save a fact, document, or event to long-term archival memory. Use for things that don't need to be in active context.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The content to store."},
                        "tags": {"type": "string", "description": "Comma-separated tags for categorization."}
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "archival_memory_search",
                "description": "Search long-term archival memory using semantic search.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "The semantic query string."},
                        "limit": {"type": "integer", "description": "Max results (default 5)."}
                    },
                    "required": ["query"]
                }
            }
        ]
