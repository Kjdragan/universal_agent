import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from universal_agent.tools.local_toolkit_bridge import agentmail_send_with_local_attachments_wrapper

async def run_test(size_mb, prefix):
    filename = f"/tmp/test_file_{prefix}_{size_mb}mb.txt"
    with open(filename, "wb") as f:
        f.write(b"0" * int(size_mb * 1024 * 1024))
        
    args = {
        "inboxId": "oddcity216@agentmail.to",
        "to": ["kevin.dragan@outlook.com"],
        "subject": f"Wrapper Test {size_mb}MB",
        "html": f"<p>This evaluates the {size_mb}MB limit. Expect large if > 4.0MB, else attachment.</p>",
        "attachment_paths": [filename]
    }
    
    print(f"\n--- Testing {size_mb}MB attachment ---")
    res = await agentmail_send_with_local_attachments_wrapper.handler(args)
    print(f"Result for {size_mb}MB:\n", res)

async def main():
    await run_test(3.5, "small")
    await run_test(4.5, "large")

if __name__ == "__main__":
    asyncio.run(main())
