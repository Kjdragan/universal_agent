import os
import time
from typing import Optional, Dict, Any


class WorkbenchBridge:
    """
    Handles the transfer of "State" (Files/Context) between the Local Agent Environment
    and the Remote Composio Workbench.
    """

    def __init__(self, composio_client, user_id=None):
        self.client = composio_client
        self.user_id = user_id
        self.sandbox_id = None

    def _ensure_sandbox(self, session_id: Optional[str] = None) -> str:
        """
        Ensures a sandbox is created/retrieved.
        If session_id is provided, it uses that sessions's sandbox or joins that session?
        Composio SDK `execute` with `session_id` handles the routing.
        We just need to store the ID if we are persisting it.
        """
        if session_id:
            # If a session ID is provided, we use it directly in future calls?
            # Or we assume the user passed it to the specific method.
            # We don't necessarily overwrite self.sandbox_id unless we want to lock to it.
            # But the 'sandbox_id' param in execute is legacy?
            # If we pass 'session_id' to execute, we might not need 'sandbox_id'.
            pass

        if self.sandbox_id:
            return self.sandbox_id

            # if we don't pass it (SDK might manage it)?
            # But Schema said REQUIRED for file ops.
            # Let's hope the SDK cache it or the response has it.
            print(
                f"   ⚠️ Warning: Could not extract sandbox_id from init response: {resp}"
            )
        else:
            print(f"   ✅ Sandbox Active: {self.sandbox_id}")

        return self.sandbox_id

    def download(
        self, remote_path: str, local_path: str, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Download a file from the remote Composio Workbench (CodeInterpreter) to the local file system.

        Args:
            remote_path: Absolute path to the file on the remote workbench.
            local_path: Path where the file should be saved locally.
            session_id: Optional Composio session ID to target a specific sandbox.
        """
        print(f"🌉 [BRIDGE] Downloading: {remote_path} -> {local_path}")
        self._ensure_sandbox(session_id)

        try:
            # Session context is maintained by the Composio client automatically
            # session_id is NOT a valid parameter for tools.execute()
            resp = self.client.tools.execute(
                slug="CODEINTERPRETER_GET_FILE_CMD",
                arguments={"file_path": remote_path},
                user_id=self.user_id,
                dangerously_skip_version_check=True,
            )

            if hasattr(resp, "model_dump"):
                resp = resp.model_dump()

            # Composio SDK with file_download_dir automatically downloads files
            # Response format: {data: {file: {uri: "/local/path", file_downloaded: true, s3url: "...", mimeType: "..."}}}
            downloaded_file = None
            if isinstance(resp, dict):
                # Navigate nested structure
                data = resp.get("data", {})
                file_info = data.get("file", {})

                if isinstance(file_info, dict):
                    downloaded_file = file_info.get("uri")
                    file_downloaded = file_info.get("file_downloaded", False)

                    if (
                        downloaded_file
                        and file_downloaded
                        and os.path.exists(downloaded_file)
                    ):
                        # Copy to target location
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
                        import shutil

                        shutil.copy2(downloaded_file, local_path)
                        size = os.path.getsize(local_path)
                        print(f"   ✅ Downloaded {size} bytes via SDK auto-download")
                        return {
                            "local_path": local_path,
                            "size": size,
                            "source": downloaded_file,
                        }

            # Fallback if auto-download didn't work
            print(f"   ❌ Auto-download failed. Response: {resp}")
            return {
                "error": "Auto-download failed - check file_download_dir configuration",
                "response": resp,
            }

        except Exception as e:
            print(f"   ❌ Download Failed: {e}")
            return {"error": f"Download Failed: {e}"}

    def upload(
        self, local_path: str, remote_path: str, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload a file from the local file system to the remote Composio Workbench.

        Args:
            local_path: Path to the local file to upload.
            remote_path: Absolute path where the file should be saved on the remote workbench.
            session_id: Optional Composio session ID to target a specific sandbox.
        """
        print(f"🌉 [BRIDGE] Uploading: {local_path} -> {remote_path}")
        self._ensure_sandbox(session_id)

        # Read local file content
        try:
            with open(local_path, "r") as f:
                content = f.read()
        except UnicodeDecodeError:
            # Fallback for binary? CodeInterpreter 'create_file' expects content string usually.
            # For POC we stick to text.
            print(
                f"   ❌ Binary file upload not supported in this version for {local_path}"
            )
            return {"error": "Binary file upload not supported in this version"}
        except FileNotFoundError:
            print(f"   ❌ Local file not found: {local_path}")
            return {"error": f"Local file not found: {local_path}"}

        try:
            # Session context is maintained by the Composio client automatically
            # session_id is NOT a valid parameter for tools.execute()
            response = self.client.tools.execute(
                slug="CODEINTERPRETER_CREATE_FILE_CMD",
                arguments={"file_path": remote_path, "content": content},
                user_id=self.user_id,
                dangerously_skip_version_check=True,
            )

            if hasattr(response, "model_dump"):
                response = response.model_dump()

            # Check success
            resp = response  # Use response from above
            if isinstance(resp, dict) and (
                resp.get("error") or resp.get("status") == "error"
            ):
                print(f"   ❌ Upload Error: {resp}")
                return {"error": resp}

            print("   ✅ Upload Success")
            return {"success": True}

            print("   ✅ Upload Success")
            return True

        except Exception as e:
            print(f"   ❌ Upload Failed: {e}")
            return False
