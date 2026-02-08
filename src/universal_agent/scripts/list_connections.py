
import os
import sys

# Add project root to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from composio import Composio

def list_connections():
    # Load API key
    api_key = os.environ.get("COMPOSIO_API_KEY")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv()
        api_key = os.environ.get("COMPOSIO_API_KEY")

    if not api_key:
        print("❌ No COMPOSIO_API_KEY found.")
        return

    print(f"🔑 Using API Key: {api_key[:5]}...{api_key[-5:]}")
    
    client = Composio(api_key=api_key)
    
    try:
        print("\n🔍 Fetching Connected Accounts (basic)...")
        connections = client.connected_accounts.list()
        
        items = getattr(connections, 'items', connections)
        
        if not items:
            print("⚠️ No connected accounts found.")
        else:
            print(f"✅ Found {len(items)} connections:")
            for item in items:
                app_name = item.toolkit.slug if hasattr(item, 'toolkit') and item.toolkit else "unknown"
                status = getattr(item, 'status', 'UNKNOWN')
                user_id = getattr(item, 'user_id', 'unknown')
                print(f"  - User: {user_id:<30} | App: {app_name:<20} | Status: {status}")

    except Exception as e:
        print(f"❌ Error fetching connections: {e}")

if __name__ == "__main__":
    list_connections()
