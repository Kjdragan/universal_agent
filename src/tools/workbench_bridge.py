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

    def _ensure_sandbox(self, keep_alive: int = 900) -> str:
        """
        Ensure a CodeInterpreter sandbox exists and cache its sandbox_id for reuse.

        Note: CodeInterpreter is NO_AUTH but still requires a user_id for Composio routing
        in most deployments. If user_id is None, this will still be attempted.
        """
        if self.sandbox_id:
            return self.sandbox_id

        resp = self.client.tools.execute(
            slug="CODEINTERPRETER_CREATE_SANDBOX",
            arguments={"keep_alive": int(keep_alive)},
            user_id=self.user_id,
            dangerously_skip_version_check=True,
        )
        if hasattr(resp, "model_dump"):
            resp = resp.model_dump()

        sandbox_id = None
        if isinstance(resp, dict):
            data = resp.get("data") or {}
            if isinstance(data, dict):
                sandbox_id = data.get("sandbox_id") or data.get("id")

        if not sandbox_id:
            raise RuntimeError(f"Could not extract sandbox_id from response: {resp}")

        self.sandbox_id = str(sandbox_id)
        return self.sandbox_id

    def download(
        self, remote_path: str, local_path: str
    ) -> Dict[str, Any]:
        """
        Download a file from the CodeInterpreter sandbox to the local file system.

        Args:
            remote_path: Absolute path to the file on the remote workbench.
            local_path: Path where the file should be saved locally.
        """
        print(f"🌉 [BRIDGE] Downloading: {remote_path} -> {local_path}")
        sandbox_id = self._ensure_sandbox()

        try:
            resp = self.client.tools.execute(
                slug="CODEINTERPRETER_GET_FILE_CMD",
                arguments={"file_path": remote_path, "sandbox_id": sandbox_id},
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

                # Common Composio SDK behavior: returns a local downloaded file path as a string.
                if isinstance(data, dict) and isinstance(data.get("file"), str):
                    downloaded_file = data["file"]
                    if downloaded_file and os.path.exists(downloaded_file):
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)
                        import shutil

                        shutil.copy2(downloaded_file, local_path)
                        size = os.path.getsize(local_path)
                        print(f"   ✅ Downloaded {size} bytes via SDK file path")
                        return {
                            "local_path": local_path,
                            "size": size,
                            "source": downloaded_file,
                            "method": "sdk_file_path",
                        }

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
        self, local_path: str, remote_path: str, overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        Upload a file to the CodeInterpreter sandbox (to /home/user/...).
        """
        print(f"🌉 [BRIDGE] Uploading: {local_path} -> {remote_path}")
        sandbox_id = self._ensure_sandbox()

        try:
            with open(local_path, "rb") as f:
                content_bytes = f.read()

            encoded = base64.b64encode(content_bytes).decode("utf-8")

            response = self.client.tools.execute(
                slug="CODEINTERPRETER_UPLOAD_FILE_CMD",
                arguments={
                    "destination_path": remote_path,
                    "file": {"name": os.path.basename(local_path), "content": encoded},
                    "overwrite": bool(overwrite),
                    "sandbox_id": sandbox_id,
                },
                user_id=self.user_id,
                dangerously_skip_version_check=True,
            )

            if hasattr(response, "model_dump"):
                response = response.model_dump()

            return {"success": True, "response": response}

        except Exception as e:
            print(f"   ❌ Upload Failed: {e}")
            return {"error": f"Upload Failed: {e}", "success": False}
