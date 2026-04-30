import os
import requests
import time
from universal_agent.infisical_loader import initialize_runtime_secrets
from universal_agent.services.youtube_playlist_manager import _get_access_token

initialize_runtime_secrets()

video_ids = [
    "XSmI7OYd7iM", "MDtMwKcx_4E", "Qn2c_U-cWQs", "QmdCkaTM1P4", "0h7Gnjm6VQk",
    "M0WsMnnNlKE", "bFO0uAMPx1g", "gRcBu8LyfGo", "LAOXy3DLyPg", "03DjE7j0Suw",
    "ZhMGNlCU4qc", "nXafozNIk3c", "n4EVksU_EOs", "l7eJfXmaCjc", "EN7frwQIbKc",
    "0UnZnonMN9o", "SFaOwbWGbY8", "aUlhaeb0o4w", "5L_tYKt2ENo", "RZPAeep6XDA"
]

playlist_id = os.environ.get('MONDAY_YT_PLAYLIST', '')

for vid in video_ids:
    token = _get_access_token()
    url = "https://www.googleapis.com/youtube/v3/playlistItems?part=snippet"
    payload = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": vid
            }
        }
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        print(f"Added {vid}")
    else:
        print(f"Failed {vid}: {response.text}")
    time.sleep(0.5)
print("Repopulation complete.")
