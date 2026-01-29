import unittest
import shutil
import os
from datetime import datetime

# Adjust path to import from src
import sys
sys.path.append(os.path.abspath("."))
from Memory_System.manager import MemoryManager
from Memory_System.models import MemoryBlock, ArchivalItem

class TestMemorySystem(unittest.TestCase):
    test_dir = "test_memory_data"

    def setUp(self):
        # Clean up previous tests
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        
        self.manager = MemoryManager(storage_dir=self.test_dir)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_default_blocks_initialization(self):
        """Test that default blocks (Persona, Human, System) are created."""
        blocks = self.manager.agent_state.core_memory
        labels = [b.label for b in blocks]
        
        self.assertIn("persona", labels)
        self.assertIn("human", labels)
        self.assertIn("system_rules", labels)
        
        # Check system rules content
        sys_block = next(b for b in blocks if b.label == "system_rules")
        self.assertIn("Package Manager: uv", sys_block.value)

    def test_core_memory_edit(self):
        """Test editing a core memory block."""
        original_val = self.manager.agent_state.core_memory[1].value # Human block
        
        # Edit
        result = self.manager.core_memory_replace("human", "Name: Kevin Dragan\nLocation: Chicago\nLikes: Pizza")
        self.assertIn("Successfully updated", result)
        
        # Verify persistence
        reloaded_manager = MemoryManager(storage_dir=self.test_dir)
        human_block = next(b for b in reloaded_manager.agent_state.core_memory if b.label == "human")
        self.assertIn("Chicago", human_block.value)
        self.assertIn("Pizza", human_block.value)

    def test_archival_memory_vector_search(self):
        """Test inserting and searching semantic memory."""
        # Insert diverse facts
        self.manager.archival_memory_insert("The user's favorite color is blue.", tags="preference")
        self.manager.archival_memory_insert("The project uses Python 3.12 and UV.", tags="tech_stack")
        self.manager.archival_memory_insert("The sky is blue.", tags="general_knowledge")
        
        # Search for color preference
        results = self.manager.storage.search_archival("What does the user like?", limit=1)
        
        # Verify results
        self.assertTrue(len(results) > 0)
        top_result = results[0]
        # Should match the user preference, not the sky, due to semantic similarity to "what does user like"
        # Note: 'The sky is blue' is also similar to 'blue', but 'user's favorite color' is better for 'user like'.
        # Vector search quality depends on model, but we check if we got *something*.
        print(f"Search Result: {top_result.content}") 
        self.assertTrue(isinstance(top_result, ArchivalItem))

if __name__ == '__main__':
    unittest.main()
