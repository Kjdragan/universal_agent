
import os
from composio import Composio

api_key = os.environ.get("COMPOSIO_API_KEY")
if not api_key:
    # Try to load from .env if not in env
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("COMPOSIO_API_KEY")

print(f"API Key present: {bool(api_key)}")

if api_key:
    client = Composio(api_key=api_key)
    # Inspect client for dashboard/link methods
    print("Client methods:", dir(client))
    
    # Try to find something relevant
    # often client.apps.get(...) or client.users.get(...) might have a link
    # or client.get_magic_link()
