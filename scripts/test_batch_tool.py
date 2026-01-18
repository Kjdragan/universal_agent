
import concurrent.futures
import time
import sys
import json

# Mock globals
def mock_search_tool(query):
    time.sleep(1) # Simulate network delay
    return f"Result for {query}"

def start_browser(url):
    return f"Browser started for {url}"

globals()["mcp__local_toolkit__search"] = mock_search_tool
globals()["mcp__local_toolkit__open_browser_url"] = start_browser

# Mock Composio client
class MockAction:
    def execute(self, args):
        time.sleep(0.5)
        return {"status": "success", "data": "composio_result"}

class MockClient:
    def action(self, name):
        return MockAction()

client = MockClient()

def batch_tool_execute(tool_calls):
    # GUARDRAIL: Limit batch size to prevent resource exhaustion
    MAX_BATCH_SIZE = 20
    if len(tool_calls) > MAX_BATCH_SIZE:
         return [{"error": f"Batch size of {len(tool_calls)} exceeds maximum limit of {MAX_BATCH_SIZE}. Please split into smaller batches."}]

    sys.stderr.write(f"[Local Toolkit] Parallel Batch executing {len(tool_calls)} calls (max 10 workers)\n")
    sys.stderr.flush()

    results = [None] * len(tool_calls)
    
    def execute_single_tool_safe(index, call_data):
        name = call_data.get("tool", "")
        args = call_data.get("input", {})
        result_item = {"index": index, "tool": name, "status": "pending"}
        
        try:
            # 1. Composio Tools
            if "mcp__composio__" in name:
                action_name = name.split("mcp__composio__")[1]
                # Bridge check inside the thread? Better to get client once outside.
                resp = client.action(action_name).execute(args)
                
            # 2. Local Tools (Self-Call)
            elif "mcp__local_toolkit__" in name:
                local_name = name.split("mcp__local_toolkit__")[1]
                # SIMULATE GLOBALS LOOKUP
                func = globals().get(local_name)
                # Ensure we look up the raw function if 'mcp__local_toolkit__' prefix is not in global keys
                # Actual mcp_server.py logic:
                # local_name = name.split("mcp__local_toolkit__")[1]
                # func = globals().get(local_name)
                
                # In this test script, I defined globals with the prefix roughly or not.
                # Let's match mcp_server logic exactly:
                
                if not func:
                     # Fallback for test script simplicity
                     func = globals().get("mcp__local_toolkit__" + local_name)
                
                if not func:
                     raise ValueError(f"Local tool '{local_name}' not found")
                resp = func(**args)
            
            else:
                 raise ValueError(f"Tool '{name}' not supported")

            # Truncate large results
            resp_str = str(resp)
            if len(resp_str) > 5000:
                result_item["result"] = resp_str[:5000] + "... [TRUNCATED]"
                result_item["truncated"] = True
            else:
                result_item["result"] = resp
            
            result_item["status"] = "success"

        except Exception as e:
            result_item["status"] = "error"
            result_item["error"] = str(e)
            sys.stderr.write(f"[Batch] Item {index} failed: {e}\n")
        
        return result_item

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_index = {
            executor.submit(execute_single_tool_safe, i, call): i 
            for i, call in enumerate(tool_calls)
        }
        
        for future in concurrent.futures.as_completed(future_to_index):
            i = future_to_index[future]
            try:
                # 3 minute timeout per item total execution time
                results[i] = future.result(timeout=180)
            except Exception as exc:
                sys.stderr.write(f"[Batch] Item {i} generated an exception: {exc}\n")
                results[i] = {"index": i, "status": "error", "error": str(exc)}

    return results

def test_batch():
    print("Testing batch execution...")
    
    # payload
    tool_calls = [
        {"tool": "mcp__local_toolkit__search", "input": {"query": f"item_{i}"}}
        for i in range(15)
    ]
    
    start = time.time()
    results = batch_tool_execute(tool_calls)
    end = time.time()
    
    print(f"Execution took {end - start:.2f} seconds")
    # print(json.dumps(results, indent=2))
    
    # 15 items * 1 sec sleep / 10 workers = should take ~2 seconds (2 rounds)
    # If sequential, would take 15 seconds.
    
    if (end - start) > 4.0:
        print("FAIL: Execution took too long (Parallelism not working)")
        sys.exit(1)
    else:
        print("PASS: Execution was parallel")
        sys.exit(0)

if __name__ == "__main__":
    test_batch()
