
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def list_connections_raw():
    api_key = os.environ.get("COMPOSIO_API_KEY")
    if not api_key:
        print("❌ No COMPOSIO_API_KEY found.")
        return

    url = "https://backend.composio.dev/api/v3/connected_accounts"
    headers = {"x-api-key": api_key}
    
    print(f"🔍 Calling {url}...")
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=headers)
            
        if response.status_code == 200:
            data = response.json()
            items = data.get('items', [])
            print(f"✅ Found {len(items)} connections:")
            for item in items:
                app_slug = item.get('toolkit', {}).get('slug', 'unknown')
                status = item.get('status', 'UNKNOWN')
                user_id = item.get('user_id', 'unknown')
                print(f"  - User: {user_id:<30} | App: {app_slug:<20} | Status: {status}")
        else:
            print(f"❌ API Error {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"❌ Network Error: {e}")

if __name__ == "__main__":
    list_connections_raw()
