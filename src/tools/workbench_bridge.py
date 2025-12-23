import os
import time
import base64
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
            # Response format A (Auto-download): {data: {file: {uri: "/local/path", file_downloaded: true, ...}}}
            # Response format B (Content return): {data: {content: "file content..."}}
            downloaded_file = None
            if isinstance(resp, dict):
                data = resp.get("data", {})

                # Check for direct content (Format B)
                if "content" in data:
                    content = data["content"]
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    with open(local_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    size = len(content)
                    print(f"   ✅ Downloaded {size} bytes via Direct Content")
                    return {
                        "local_path": local_path,
                        "size": size,
                        "method": "direct_content",
                    }

                # Check for auto-download (Format A)
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
                            "method": "sdk_auto",
                        }

            # Fallback if neither worked
            print(f"   ❌ Auto-download failed. Response: {resp}")
            return {
                "error": "Auto-download failed - no 'content' or 'file_downloaded' in response",
                "response": resp,
            }

        except Exception as e:
            print(f"   ❌ Download Failed: {e}")
            return {"error": f"Download Failed: {e}"}

    def upload(
        self, local_path: str, remote_path: str, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload a file using Python execution on the remote workbench.
        Adapted to use COMPOSIO_REMOTE_WORKBENCH since specific file tools may be missing.
        """
        print(f"🌉 [BRIDGE] Uploading: {local_path} -> {remote_path}")
        self._ensure_sandbox(session_id)

        try:
            # Read local file content
            with open(local_path, "rb") as f:
                content_bytes = f.read()

            # Base64 encode
            encoded = base64.b64encode(content_bytes).decode("utf-8")

            # Python script to decode and write
            script = (
                "import os, base64\n"
                f"path = '{remote_path}'\n"
                "os.makedirs(os.path.dirname(path), exist_ok=True)\n"
                f"with open(path, 'wb') as f: f.write(base64.b64decode('{encoded}'))\n"
                "print(f'Successfully uploaded to {path}')"
            )

            payload = {
                "code_to_execute": script,
            }
            if session_id:
                payload["session_id"] = session_id

            # Execute via Remote Workbench
            response = self.client.tools.execute(
                slug="COMPOSIO_REMOTE_WORKBENCH",
                arguments=payload,
                user_id=self.user_id,
                dangerously_skip_version_check=True,
            )

            if hasattr(response, "model_dump"):
                response = response.model_dump()

            # Check for execution errors in response structure
            execution_data = {}
            if hasattr(response, "data"): # Handle response object
                execution_data = response.data
            elif isinstance(response, dict):
                execution_data = response.get("data", {})

            # Check stdout for success message
            stdout = execution_data.get("stdout", "")
            stderr = execution_data.get("stderr", "")
            
            if "Successfully uploaded" not in stdout and not execution_data.get("success"):
                 print(f"   ❌ Upload Script Failed. Stdout: {stdout}, Stderr: {stderr}")
                 return {"success": False, "error": f"Upload Script Failed: {stderr or stdout}"}

            # Double Check: Verify file exists
            verify_script = f"import os; print('EXISTS' if os.path.exists('{remote_path}') else 'MISSING')"
            verify_resp = self.client.tools.execute(
                slug="COMPOSIO_REMOTE_WORKBENCH",
                arguments={"code_to_execute": verify_script, "session_id": session_id} if session_id else {"code_to_execute": verify_script},
                user_id=self.user_id,
                dangerously_skip_version_check=True
            )
            
            verify_stdout = ""
            if hasattr(verify_resp, "data"):
                 verify_stdout = verify_resp.data.get("stdout", "")
            elif isinstance(verify_resp, dict):
                 verify_stdout = verify_resp.get("data", {}).get("stdout", "")

            if "EXISTS" not in verify_stdout:
                print(f"   ❌ Verification Failed: File not found at {remote_path}")
                return {"success": False, "error": f"Verification Failed: File not found at {remote_path}"}

            print("   ✅ Upload Verified (Exists on Remote)")
            return {"success": True}

        except Exception as e:
            print(f"   ❌ Upload Failed: {e}")
            return {"error": f"Upload Failed: {e}", "success": False}
