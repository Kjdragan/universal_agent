
import os
import sys

# Add project root to path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))

from composio import Composio

# Load API key
api_key = os.environ.get("COMPOSIO_API_KEY")
if not api_key:
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.environ.get("COMPOSIO_API_KEY")

if api_key:
    client = Composio(api_key=api_key)
    print("Client methods:", dir(client))
    # Maybe it's client.users.get(slug=user_id)? 
    # Or client.get_entity() was deprecated?
