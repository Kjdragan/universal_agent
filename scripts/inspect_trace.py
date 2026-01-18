import json
import sys

TRACE_FILE = '/home/kjdragan/lrepos/universal_agent/urw_sessions/session_20260117_190737_9ab7110b/trace.json'

def inspect_structure(d, indent=0):
    prefix = "  " * indent
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, list):
                print(f"{prefix}{k}: List[{len(v)}]")
                if len(v) > 0 and indent < 2:
                    inspect_structure(v[0], indent+1)
            elif isinstance(v, dict):
                print(f"{prefix}{k}: Dict")
                if indent < 2:
                    inspect_structure(v, indent+1)
            else:
                print(f"{prefix}{k}: {type(v).__name__}")

def inspect_trace():
    try:
        with open(TRACE_FILE, 'r') as f:
            data = json.load(f)
        
        print("--- Structure ---")
        inspect_structure(data)
        
        print("\n--- Searching for Batch & Search ---")
        # Flatten and search
        def recursive_search(obj):
            if isinstance(obj, dict):
                if 'tool_calls' in obj:
                    for tc in obj['tool_calls']:
                        func = tc.get('function', {})
                        name = func.get('name', '')
                        args = func.get('arguments', '')
                        if 'batch' in name or 'search' in name or 'google' in args or 'browse' in name:
                             print(f"FOUND: {name} | Args: {args[:100]}...")
                for k, v in obj.items():
                    recursive_search(v)
            elif isinstance(obj, list):
                for item in obj:
                    recursive_search(item)

        recursive_search(data)

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_trace()
