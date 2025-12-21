from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import sys

# Ensure src path for imports
sys.path.append(os.path.abspath("src"))
from tools.workbench_bridge import WorkbenchBridge
from composio import Composio

# Initialize Configuration
load_dotenv()

try:
    mcp = FastMCP("Local Intelligence Toolkit")
except Exception:
    raise

# Initialize Bridge Lazy (or global)
# We need a Composio client context.
# In a real tool call, we might want to pass session info?
# For now, we use the environment variable config.


def get_bridge():
    # Instantiate fresh or cached
    client = Composio(api_key=os.environ.get("COMPOSIO_API_KEY"))
    # User ID defaults to standard; in production we might need to parameterize this
    return WorkbenchBridge(composio_client=client, user_id="user_123")


@mcp.tool()
def workbench_download(
    remote_path: str, local_path: str, session_id: str = None
) -> str:
    """
    Download a file from the Remote Composio Workbench to the Local Workspace.

    Args:
        remote_path: The absolute path of the file on the remote workbench (e.g. /home/user/data.csv)
        local_path: The relative or absolute path where to save it locally (e.g. ./data.csv)
        session_id: OPTIONAL. The Composio Session ID where the file exists.
                    If you are working in a specific session (e.g. from 'COMPOSIO_REMOTE_WORKBENCH'), provide it here.
    """
    bridge = get_bridge()
    result = bridge.download(remote_path, local_path, session_id=session_id)
    if result.get("error"):
        return f"Error: {result['error']}"
    return f"Successfully downloaded {remote_path} to {local_path}. Local path: {result.get('local_path')}"


@mcp.tool()
def workbench_upload(local_path: str, remote_path: str, session_id: str = None) -> str:
    """
    Upload a file from the Local Workspace to the Remote Composio Workbench.

    Args:
        local_path: The path of the local file to upload (e.g. ./report.md)
        remote_path: The absolute destination path on the remote workbench (e.g. /home/user/report.md)
        session_id: OPTIONAL. The Composio Session ID where to upload the file.
    """
    bridge = get_bridge()
    result = bridge.upload(local_path, remote_path, session_id=session_id)
    if result.get("error"):
        return f"Error: {result['error']}"
    return f"Successfully uploaded {local_path} to {remote_path}."


if __name__ == "__main__":
    mcp.run()
