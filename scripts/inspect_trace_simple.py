import json

TRACE_FILE = '/home/kjdragan/lrepos/universal_agent/urw_sessions/session_20260117_190737_9ab7110b/trace.json'

def inspect():
    try:
        with open(TRACE_FILE, 'r') as f:
            data = json.load(f)
        
        # Check root tool_calls
        if 'tool_calls' in data:
            print(f"Found {len(data['tool_calls'])} tool calls in root.")
            for tc in data['tool_calls']:
                name = tc.get('name', 'UNKNOWN')
                args = tc.get('input', {})
                # Filter for relevant tools
                if any(x in name for x in ['batch', 'search', 'google', 'browse', 'crawl', 'scrape']):
                    print(f"\nTool: {name}")
                    print(f"Args: {str(args)[:500]}") # Truncate long args
        else:
            print("No 'tool_calls' list in root.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect()
