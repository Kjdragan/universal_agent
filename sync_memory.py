
import os
import sys

# Ensure we can import from src
sys.path.append(os.path.abspath("src"))
sys.path.append(os.path.abspath("."))

from Memory_System.manager import MemoryManager

def run_sync():
    print("🔄 Starting Manual Memory Sync...")
    
    # Initialize Manager (points to default storage in Memory_System_Data)
    mgr = MemoryManager()
    
    # Read the file
    mem_file = "memory/MEMORY.md"
    if not os.path.exists(mem_file):
        print(f"❌ File not found: {mem_file}")
        return
        
    with open(mem_file, "r") as f:
        content = f.read()
        
    print(f"📖 Read {len(content)} bytes from {mem_file}")
    
    # Trigger the sync logic we just wrote
    mgr._sync_core_blocks_from_markdown(content)
    
    # Verify
    persona = mgr.get_memory_block("persona")
    print("\n✅ Current Database State:")
    print(f"PERSONA: {persona.value[:100]}...")
    
    print("\n🎉 Sync Complete successfully.")

if __name__ == "__main__":
    run_sync()
