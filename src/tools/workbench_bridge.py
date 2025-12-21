import os
import time


class WorkbenchBridge:
    """
    Handles the transfer of "State" (Files/Context) between the Local Agent Environment
    and the Remote Composio Workbench.
    """

    def __init__(self, composio_client, user_id=None):
        self.client = composio_client
        self.user_id = user_id
        self.sandbox_id = None

    def _ensure_sandbox(self):
        """
        Ensures we have a valid sandbox ID.
        If we don't have one, we run a trivial command to create one.
        """
        if self.sandbox_id:
            return self.sandbox_id

        print("   ⏳ Initializing Sandbox...")
        resp = self.client.tools.execute(
            slug="CODEINTERPRETER_RUN_TERMINAL_CMD",
            arguments={"command": "echo 'Sandbox Init'"},
            user_id=self.user_id,
            dangerously_skip_version_check=True,
        )

        # Extract sandbox_id from response
        # It seems Composio returns the output directly if generic, or a detailed dict.
        # Trace suggests it returns a Model object or dict.
        if hasattr(resp, "model_dump"):
            resp = resp.model_dump()

        if isinstance(resp, dict):
            # Try to find sandbox_id in common places
            self.sandbox_id = resp.get("sandbox_id") or resp.get("data", {}).get(
                "sandbox_id"
            )
            # If not found, maybe it's in the 'meta' or 'execution_details'?
            # Actually, for CODEINTERPRETER, the first run creates it.
            # We might need to parse it or just rely on the fact that we passed 'user_id'.
            pass

        if not self.sandbox_id:
            # Fallback: Maybe it's not returned explicitly, but subsequent calls might work
            # if we don't pass it (SDK might manage it)?
            # But Schema said REQUIRED for file ops.
            # Let's hope the SDK cache it or the response has it.
            print(
                f"   ⚠️ Warning: Could not extract sandbox_id from init response: {resp}"
            )
        else:
            print(f"   ✅ Sandbox Active: {self.sandbox_id}")

        return self.sandbox_id

    def download(self, remote_path: str, local_path: str) -> bool:
        """
        Syncs a file from Remote Registry -> Local Agent.
        """
        print(f"🌉 [BRIDGE] Downloading: {remote_path} -> {local_path}")
        sandbox_id = self._ensure_sandbox()

        # Note: We can try running without sandbox_id if the user_id implies a session?
        # But schema said required. Let's try passing it only if we have it.

        args = {"file_path": remote_path}
        if sandbox_id:
            args["sandbox_id"] = sandbox_id

        try:
            resp = self.client.tools.execute(
                slug="CODEINTERPRETER_GET_FILE_CMD",
                arguments=args,
                user_id=self.user_id,
                dangerously_skip_version_check=True,
            )

            if hasattr(resp, "model_dump"):
                resp = resp.model_dump()

            # Check if response points to a local file (Composio SDK behavior)
            downloaded_path = None
            if isinstance(resp, dict):
                downloaded_path = resp.get("data", {}).get("file")

            if downloaded_path and os.path.exists(downloaded_path):
                # Move/Copy it to destination
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                import shutil

                shutil.copy2(downloaded_path, local_path)
                size = os.path.getsize(local_path)
                print(f"   ✅ Downloaded {size} bytes from {downloaded_path}.")
                return True

            # Fallback: Content in response
            content = None
            if isinstance(resp, dict):
                content = resp.get("content") or resp.get("data", {}).get("content")
            if isinstance(resp, str):
                content = resp

            if content:
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "w") as f:
                    f.write(content)
                print(f"   ✅ Downloaded {len(content)} bytes (from content).")
                return True
            else:
                print(f"   ❌ Empty content or error: {resp}")
                return False

        except Exception as e:
            print(f"   ❌ Download Failed: {e}")
            return False

    def upload(self, local_path: str, remote_path: str) -> bool:
        """
        Syncs a file from Local Agent -> Remote Registry.
        """
        print(f"🌉 [BRIDGE] Uploading: {local_path} -> {remote_path}")
        sandbox_id = self._ensure_sandbox()

        if not os.path.exists(local_path):
            print(f"   ❌ Local file not found: {local_path}")
            return False

        args = {"file": local_path, "destination_path": remote_path, "overwrite": True}
        if sandbox_id:
            args["sandbox_id"] = sandbox_id

        try:
            resp = self.client.tools.execute(
                slug="CODEINTERPRETER_UPLOAD_FILE_CMD",
                arguments=args,
                user_id=self.user_id,
                dangerously_skip_version_check=True,
            )

            if hasattr(resp, "model_dump"):
                resp = resp.model_dump()

            # Check success
            if isinstance(resp, dict) and (
                resp.get("error") or resp.get("status") == "error"
            ):
                print(f"   ❌ Upload Error: {resp}")
                return False

            print("   ✅ Upload Success")
            return True

        except Exception as e:
            print(f"   ❌ Upload Failed: {e}")
            return False
