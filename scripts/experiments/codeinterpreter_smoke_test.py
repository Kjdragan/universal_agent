"""
Smoke test for Composio CodeInterpreter toolkit slugs.

Goals:
- Validate that the expected slugs exist and work with our auth/config.
- Demonstrate sandbox reuse via sandbox_id.
- Validate file roundtrip: write /home/user/smoke.txt then fetch it.

Usage:
  uv run python scripts/experiments/codeinterpreter_smoke_test.py

Environment:
  COMPOSIO_API_KEY (required)
  DEFAULT_USER_ID or COMPOSIO_USER_ID (recommended)
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

from composio import Composio


def _get_user_id() -> str | None:
    for k in ("DEFAULT_USER_ID", "COMPOSIO_USER_ID"):
        v = (os.getenv(k) or "").strip()
        if v:
            return v
    return None


def _as_dict(resp):
    if hasattr(resp, "model_dump"):
        return resp.model_dump()
    return resp


def _extract_sandbox_id(resp_dict: dict) -> str | None:
    data = resp_dict.get("data") or {}
    for key in ("sandbox_id", "id"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # Some responses nest identifiers; handle conservative fallbacks.
    meta = resp_dict.get("meta") or {}
    v = meta.get("sandbox_id")
    if isinstance(v, str) and v.strip():
        return v.strip()
    return None


def main() -> int:
    api_key = (os.getenv("COMPOSIO_API_KEY") or "").strip()
    if not api_key:
        print("ERROR: COMPOSIO_API_KEY is not set", file=sys.stderr)
        return 2

    user_id = _get_user_id()
    if not user_id:
        print("WARN: DEFAULT_USER_ID/COMPOSIO_USER_ID not set; proceeding without user_id.", file=sys.stderr)

    client = Composio(api_key=api_key)

    # 1) Create sandbox
    create = client.tools.execute(
        slug="CODEINTERPRETER_CREATE_SANDBOX",
        arguments={"keep_alive": 900},
        user_id=user_id,
        dangerously_skip_version_check=True,
    )
    create_d = _as_dict(create)
    sandbox_id = _extract_sandbox_id(create_d) or ""
    if not sandbox_id:
        print(f"ERROR: Could not extract sandbox_id from create response: {create_d}", file=sys.stderr)
        return 3

    print(f"OK: sandbox_id={sandbox_id}")

    # 2) Execute code: write a file under /home/user
    code = (
        "from pathlib import Path\n"
        "p = Path('/home/user/smoke.txt')\n"
        "p.write_text('hello from codeinterpreter\\n', encoding='utf-8')\n"
        "print('WROTE', str(p))\n"
    )
    exec_resp = client.tools.execute(
        slug="CODEINTERPRETER_EXECUTE_CODE",
        arguments={"code_to_execute": code, "sandbox_id": sandbox_id, "keep_alive": 900, "timeout": 120},
        user_id=user_id,
        dangerously_skip_version_check=True,
    )
    exec_d = _as_dict(exec_resp)
    print("OK: execute_code")

    # 3) Verify via terminal command
    ls_resp = client.tools.execute(
        slug="CODEINTERPRETER_RUN_TERMINAL_CMD",
        arguments={"command": "ls -la /home/user && sed -n '1,5p' /home/user/smoke.txt", "sandbox_id": sandbox_id, "keep_alive": 900, "timeout": 120},
        user_id=user_id,
        dangerously_skip_version_check=True,
    )
    ls_d = _as_dict(ls_resp)
    print("OK: run_terminal_cmd")

    # 4) Fetch file
    get_resp = client.tools.execute(
        slug="CODEINTERPRETER_GET_FILE_CMD",
        arguments={"file_path": "/home/user/smoke.txt", "sandbox_id": sandbox_id, "timeout": 120},
        user_id=user_id,
        dangerously_skip_version_check=True,
    )
    get_d = _as_dict(get_resp)

    # Try to decode content in common shapes.
    data = get_d.get("data") or {}
    content = None
    if isinstance(data, dict):
        # direct content
        if isinstance(data.get("content"), str):
            content = data["content"]
        # base64 content
        if content is None and isinstance(data.get("file"), dict):
            f = data["file"]
            if isinstance(f.get("content"), str):
                # Some toolkits return base64 in file.content
                try:
                    content = base64.b64decode(f["content"]).decode("utf-8", errors="replace")
                except Exception:
                    content = f["content"]

    if content is None:
        print(f"WARN: could not extract file content; raw response keys: {list(get_d.keys())}", file=sys.stderr)
    else:
        print("OK: get_file_cmd (content extracted)")

    # 5) Write local artifact
    out_dir = Path("work_products") / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "codeinterpreter_smoke.txt"
    out_path.write_text(
        f"sandbox_id={sandbox_id}\n\n--- execute_code ---\n{exec_d}\n\n--- run_terminal_cmd ---\n{ls_d}\n\n--- get_file_cmd ---\n{get_d}\n\n--- extracted_content ---\n{content or ''}\n",
        encoding="utf-8",
    )
    print(f"OK: wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

