
import os
from composio import Composio

# Load API key
api_key = os.environ.get("COMPOSIO_API_KEY")
if not api_key:
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("COMPOSIO_API_KEY")

if not api_key:
    print("❌ No COMPOSIO_API_KEY found.")
    exit(1)

print(f"🔑 Using API Key: {api_key[:5]}...{api_key[-5:]}")

client = Composio(api_key=api_key)

# Attempt to find user info
# Usually client.client.get(...) or similar for raw requests if SDK doesn't expose whoami
try:
    # Try to list connected accounts to see if there is a 'user' field in the response
    # pointing to the owner
    print("Attempting to fetch active connections to infer user...")
    connections = client.connected_accounts.list()
    if connections and hasattr(connections, 'items') and len(connections.items) > 0:
        first = connections.items[0]
        print(f"Connection sample: {first}")
        # Sometimes there is a 'user_id' or similar
    else:
        print("No connections found.")
        
except Exception as e:
    print(f"Error listing connections: {e}")

# Try direct API call to /v1/client/auth/details or similar if we can guess the endpoint
# or just print instructions.
print("\n💡 NOTE: I cannot retrieve your password. You should use your email to log in.")
print(f"API Key is configured in .env")
