
import os
import sys

FORBIDDEN_TERMS = [
    "webReader",
    "web_reader",
    "api.z.ai",
    "mcp__web_reader"
]

EXCLUDED_DIRS = {
    "__pycache__",
    ".git",
    ".gemini",
    "AGENT_RUN_WORKSPACES" # Exclude historical run logs
}

def scan_file(filepath):
    """Bail if file contains forbidden terms."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            for term in FORBIDDEN_TERMS:
                if term in content:
                    # Ignore if it's this test file itself
                    if "verify_no_webreader.py" in filepath:
                        continue
                    print(f"❌ FOUND '{term}' in {filepath}")
                    return False
    except Exception as e:
        print(f"⚠️ Could not read {filepath}: {e}")
    return True

def main():
    print("🔍 Scanning codebase for forbidden webReader/Z.AI terms...")
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    failure_count = 0
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Filter excluded dirs
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        
        for filename in filenames:
            ext = os.path.splitext(filename)[1]
            if ext in [".py", ".md", ".html", ".json", ".txt"]:
                filepath = os.path.join(dirpath, filename)
                if not scan_file(filepath):
                    failure_count += 1

    if failure_count > 0:
        print(f"\n❌ Verification FAILED: Found {failure_count} files with forbidden terms.")
        sys.exit(1)
    else:
        print("\n✅ Verification PASSED: No forbidden terms found.")
        sys.exit(0)

if __name__ == "__main__":
    main()
