from composio import Composio
import inspect

client = Composio(api_key="TEST")

# Check for methods related to adding tools
methods = dir(client.tools)
print("Tools methods:", methods)

# Check for 'custom_tools' or App management
print("Client attributes:", dir(client))
