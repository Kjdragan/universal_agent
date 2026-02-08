
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

def check_toolkit_connection(slug):
    api_key = os.environ.get("COMPOSIO_API_KEY")
    # Get connections for this toolkit
    url = f"https://backend.composio.dev/api/v3/connected_accounts?toolkit_slugs={slug}"
    headers = {"x-api-key": api_key}
    
    print(f"🔍 Checking connections for '{slug}'...")
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=headers)
            
        if response.status_code == 200:
            data = response.json()
            items = data.get('items', [])
            print(f"✅ Found {len(items)} connections:")
            for item in items:
                status = item.get('status', 'UNKNOWN')
                user_id = item.get('user_id', 'unknown')
                print(f"  - User: {user_id:<30} | Status: {status}")
        else:
            print(f"❌ API Error {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"❌ Network Error: {e}")

if __name__ == "__main__":
    check_toolkit_connection("gmail")
    print("-" * 40)
    check_toolkit_connection("discord")
