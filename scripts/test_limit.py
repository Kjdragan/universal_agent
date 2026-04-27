import json
import os
import urllib.error
import urllib.request


def test_size(size_mb):
    payload = {
        "to": ["kevin.dragan@outlook.com"],
        "subject": f"Test {size_mb}MB",
        "text": "test",
        "attachments": [{"filename": "dummy.txt", "content": "A" * (size_mb * 1024 * 1024)}]
    }
    api_key = os.getenv("AGENTMAIL_API_KEY", "").strip()
    url = "https://api.agentmail.to/v0/inboxes/oddcity216@agentmail.to/messages/send"
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        data = json.dumps(payload).encode("utf-8")
        with urllib.request.urlopen(req, data=data, timeout=60.0) as resp:
            print(f"{size_mb}MB SUCCESS")
    except urllib.error.HTTPError as e:
        print(f"{size_mb}MB FAILED: {e.code} API Error - {e.read().decode('utf-8')}")

import sys

size = int(sys.argv[1])
test_size(size)
