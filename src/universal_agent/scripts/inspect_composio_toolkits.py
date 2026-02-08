
import os
from composio import Composio
from dotenv import load_dotenv

load_dotenv()

# Mock user ID if needed, or use a known test user
USER_ID = "test_user" 

def inspect_toolkits():
    print(f"Inspecting Composio toolkits for user: {USER_ID}")
    
    try:
        client = Composio(api_key=os.environ.get("COMPOSIO_API_KEY"))
        print(f"Client Dir: {dir(client)}")
    except Exception as e:
        print(f"Failed to init Composio client: {e}")
        return

    try:
        # Check connected accounts without filter first to see what's there
        print("\n--- All Connected Accounts (First 5) ---")
        try:
            connections = client.connected_accounts.list() 
            if hasattr(connections, 'items'):
                for i, item in enumerate(connections.items):
                    if i >= 5: break
                    print(f"\nItem {i}:")
                    if hasattr(item, 'toolkit'):
                        print(f"  Toolkit: {item.toolkit}")
                        try:
                             # Try to dump the model
                             if hasattr(item.toolkit, 'model_dump'):
                                 print(f"  Toolkit Dump: {item.toolkit.model_dump()}")
                             elif hasattr(item.toolkit, 'dict'):
                                 print(f"  Toolkit Dict: {item.toolkit.dict()}")
                        except Exception as e:
                             print(f"  Failed to dump toolkit: {e}")
            else:
                 print("No items (iterable) in response.")
                 print(f"Response type: {type(connections)}")
                 print(f"Response dir: {dir(connections)}")
        except Exception as e:
            print(f"Error listing all accounts: {e}")

        # Inspect client.toolkits
        print("\n--- client.toolkits Inspection ---")
        if hasattr(client, 'toolkits'):
             print(f"client.toolkits Dir: {dir(client.toolkits)}")
             try:
                 gmail_tk = client.toolkits.get('gmail')
                 print(f"client.toolkits.get('gmail'): {gmail_tk}")
                 # Check for description in the returned object
                 if hasattr(gmail_tk, 'description'):
                     print(f"  Description: {gmail_tk.description}")
                 elif isinstance(gmail_tk, dict):
                     print(f"  Description (dict): {gmail_tk.get('description')}")
                 else:
                     print(f"  Dir: {dir(gmail_tk)}")
             except Exception as e:
                 print(f"client.toolkits.get failed: {e}")
        
        # Inspect client.tools
        print("\n--- client.tools Inspection ---")
        if hasattr(client, 'tools'):
             print(f"client.tools Dir: {dir(client.tools)}")

    except Exception as e:
        print(f"Error during inspection: {e}")

if __name__ == "__main__":
    inspect_toolkits()
