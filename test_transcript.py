import os
import requests

url = "http://127.0.0.1:8002/api/v1/youtube/ingest"
payload = {
    "video_id": "jNQXAC9IVRw",
    "video_url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "timeout_seconds": 90,
    "max_chars": 20000,
    "min_chars": 120
}

token = os.getenv("UA_YOUTUBE_INGEST_TOKEN") or os.getenv("SESSION_API_TOKEN") or ""
headers = {}
if token:
    headers["Authorization"] = f"Bearer {token}"
else:
    print("Warning: No token found in environment.")

try:
    resp = requests.post(url, json=payload, headers=headers)
    print("Status:", resp.status_code)
    print("Body:", resp.text)
except Exception as e:
    print("Error:", e)
