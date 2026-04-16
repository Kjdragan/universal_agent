import base64
from agentmail import AgentMail
import os
import sys

size_mb = int(sys.argv[1])
client = AgentMail(api_key=os.getenv("AGENTMAIL_API_KEY", "").strip())

encoded = base64.b64encode(b"A" * (size_mb * 1024 * 1024)).decode()
try:
    message = client.inboxes.messages.send(
        "oddcity216@agentmail.to",
        to=["kevin.dragan@outlook.com"],
        subject=f"SDK Test {size_mb}MB",
        text="See attachment",
        attachments=[{"content": encoded, "filename": "dummy.txt", "content_type": "text/plain"}],
    )
    print(f"SDK {size_mb}MB SUCCESS")
except Exception as e:
    print(f"SDK {size_mb}MB FAILED:", e)
