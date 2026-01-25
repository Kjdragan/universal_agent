import sys
import os

# Add local directory to path to find Memory_System
sys.path.append(os.getcwd())

from Memory_System.manager import MemoryManager

def main():
    # Initialize with the same default path as main.py
    # main.py does: storage_path = os.getenv("PERSIST_DIRECTORY", os.path.join(src_dir, "Memory_System_Data"))
    # but src_dir in main.py seems likely relative to root? 
    # Let's assume Memory_System_Data is in root as seen in ls.
    
    storage_path = os.path.abspath("Memory_System_Data")
    print(f"Loading memory from: {storage_path}")
    
    try:
        mem_mgr = MemoryManager(storage_dir=storage_path)
        context = mem_mgr.get_system_prompt_addition()
        print("\n=== START LETTA CONTEXT ===")
        print(context)
        print("=== END LETTA CONTEXT ===")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
