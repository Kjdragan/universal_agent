
import os
from composio import Composio
try:
    import composio
    print(f"Composio package file: {composio.__file__}")
except ImportError:
    print("Could not import composio directly to check file")

try:
    c = Composio(api_key="TEST")
    print(f"Composio object attributes: {dir(c)}")
    
    if hasattr(c, 'tools') and hasattr(c.tools, 'execute'):
        import inspect
        try:
            sig = inspect.signature(c.tools.execute)
            print(f"\nSignature of client.tools.execute: {sig}")
            print(f"Docstring: {c.tools.execute.__doc__}")
        except Exception as e:
            print(f"Could not get signature: {e}")

except Exception as e:
    print(f"Error instantiating Composio: {e}")
