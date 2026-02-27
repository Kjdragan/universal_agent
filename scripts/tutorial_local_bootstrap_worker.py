#!/usr/bin/env python3
"""
Poll tutorial bootstrap jobs from a remote gateway and execute them locally.

This is designed for desktop execution so Dashboard "Create Repo" can queue
jobs on VPS while repo generation happens on your local machine.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(message: str) -> None:
    print(f"[{_now()}] {message}", flush=True)


def _normalize_base_url(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if not value:
        raise ValueError("Gateway URL is required")
    if not value.startswith("http://") and not value.startswith("https://"):
        value = f"https://{value}"
    return value.rstrip("/")


def _request(
    *,
    method: str,
    url: str,
    ops_token: str,
    json_payload: dict[str, Any] | None = None,
    timeout_seconds: int = 60,
    expect_bytes: bool = False,
) -> Any:
    body = None
    headers = {"Accept": "application/json"}
    if ops_token:
        headers["x-ua-ops-token"] = ops_token
    if json_payload is not None:
        body = json.dumps(json_payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read()
            if expect_bytes:
                return payload
            if not payload:
                return {}
            return json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        detail = raw
        try:
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict):
                detail = str(parsed.get("detail") or parsed)
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code} on {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def _extract_repo_dir(stdout: str, stderr: str, fallback: str) -> str:
    merged = "\n".join([stdout or "", stderr or ""])
    match = re.search(r"Repo ready:\s*(.+)", merged)
    if match:
        return match.group(1).strip()
    return fallback


def _bundle_endpoint(base_url: str, job_id: str) -> str:
    job_quoted = urllib.parse.quote(job_id, safe="")
    return f"{base_url}/api/v1/ops/tutorials/bootstrap-jobs/{job_quoted}/bundle"


def _result_endpoint(base_url: str, job_id: str) -> str:
    job_quoted = urllib.parse.quote(job_id, safe="")
    return f"{base_url}/api/v1/ops/tutorials/bootstrap-jobs/{job_quoted}/result"


def _claim_endpoint(base_url: str) -> str:
    return f"{base_url}/api/v1/ops/tutorials/bootstrap-jobs/claim"


def _process_job(
    *,
    base_url: str,
    ops_token: str,
    worker_id: str,
    target_root_override: str,
    job: dict[str, Any],
) -> None:
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        _log("Skipping malformed job with missing job_id")
        return

    repo_name = str(job.get("repo_name") or "").strip()
    if not repo_name:
        repo_name = f"yt_tutorial_impl_{int(time.time())}"
    target_root = target_root_override or str(job.get("target_root") or "").strip()
    if not target_root:
        target_root = "/home/kjdragan/YoutubeCodeExamples"
    python_version = str(job.get("python_version") or "").strip()
    timeout_seconds = int(job.get("timeout_seconds") or 900)
    timeout_seconds = max(30, min(timeout_seconds, 3600))

    _log(f"Claimed job {job_id} for run {job.get('tutorial_run_path')} -> {target_root}/{repo_name}")
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"ua_tutorial_bootstrap_{job_id}_"))
    stdout = ""
    stderr = ""
    error = ""
    status = "failed"
    repo_dir = str((Path(target_root) / repo_name).resolve())

    try:
        bundle_url = _bundle_endpoint(base_url, job_id)
        bundle_bytes = _request(
            method="GET",
            url=bundle_url,
            ops_token=ops_token,
            timeout_seconds=120,
            expect_bytes=True,
        )
        bundle_path = tmp_dir / "bundle.tgz"
        bundle_path.write_bytes(bundle_bytes)

        with tarfile.open(bundle_path, mode="r:gz") as archive:
            archive.extractall(path=tmp_dir)

        implementation_dir = tmp_dir / "implementation"
        script_path = implementation_dir / "create_new_repo.sh"
        if not script_path.exists():
            raise RuntimeError("Bundle is missing implementation/create_new_repo.sh")

        cmd = ["bash", str(script_path), target_root, repo_name]
        if python_version:
            cmd.append(python_version)
        proc = subprocess.run(
            cmd,
            cwd=str(implementation_dir),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=os.environ.copy(),
            check=False,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        repo_dir = _extract_repo_dir(stdout, stderr, repo_dir)
        if proc.returncode == 0:
            status = "completed"
            _log(f"Job {job_id} completed: {repo_dir}")
        else:
            status = "failed"
            error = f"Bootstrap script exited with code {proc.returncode}"
            _log(f"Job {job_id} failed: {error}")
    except subprocess.TimeoutExpired:
        status = "failed"
        error = f"Bootstrap script timed out after {timeout_seconds}s"
        _log(f"Job {job_id} failed: {error}")
    except Exception as exc:
        status = "failed"
        error = str(exc)
        _log(f"Job {job_id} failed: {error}")
    finally:
        payload = {
            "worker_id": worker_id,
            "status": status,
            "repo_dir": repo_dir,
            "stdout": stdout[-8000:],
            "stderr": stderr[-4000:],
            "error": error[-1200:],
        }
        try:
            _request(
                method="POST",
                url=_result_endpoint(base_url, job_id),
                ops_token=ops_token,
                json_payload=payload,
                timeout_seconds=60,
            )
        except Exception as exc:
            _log(f"Failed to report result for {job_id}: {exc}")
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local tutorial repo bootstrap jobs")
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("UA_TUTORIAL_BOOTSTRAP_GATEWAY_URL")
        or os.getenv("UA_GATEWAY_URL")
        or "https://api.clearspringcg.com",
        help="Gateway base URL, for example https://api.clearspringcg.com",
    )
    parser.add_argument(
        "--ops-token",
        default=os.getenv("UA_OPS_TOKEN", ""),
        help="Ops token for protected /api/v1/ops endpoints",
    )
    parser.add_argument(
        "--target-root",
        default=os.getenv("UA_TUTORIAL_BOOTSTRAP_LOCAL_ROOT", "/home/kjdragan/YoutubeCodeExamples"),
        help="Local root directory where new repos should be created",
    )
    parser.add_argument(
        "--worker-id",
        default=os.getenv("UA_TUTORIAL_BOOTSTRAP_WORKER_ID")
        or f"{socket.gethostname()}-{os.getpid()}",
        help="Stable worker identifier",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.getenv("UA_TUTORIAL_BOOTSTRAP_POLL_SECONDS", "5") or 5),
        help="Polling interval when no jobs are queued",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim and process at most one job, then exit",
    )
    args = parser.parse_args()

    try:
        base_url = _normalize_base_url(args.gateway_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    ops_token = (args.ops_token or "").strip()
    if not ops_token:
        print("UA_OPS_TOKEN/--ops-token is required", file=sys.stderr)
        return 2

    target_root = str(args.target_root or "").strip() or "/home/kjdragan/YoutubeCodeExamples"
    Path(target_root).mkdir(parents=True, exist_ok=True)
    poll_seconds = max(1.0, float(args.poll_seconds or 5.0))

    _log(f"Starting local tutorial bootstrap worker id={args.worker_id}")
    _log(f"Gateway: {base_url}")
    _log(f"Target root: {target_root}")

    processed = 0
    while True:
        try:
            claim_payload = _request(
                method="POST",
                url=_claim_endpoint(base_url),
                ops_token=ops_token,
                json_payload={"worker_id": args.worker_id},
                timeout_seconds=60,
            )
            job = claim_payload.get("job") if isinstance(claim_payload, dict) else None
            if not isinstance(job, dict):
                if args.once:
                    _log("No queued jobs.")
                    break
                time.sleep(poll_seconds)
                continue
            _process_job(
                base_url=base_url,
                ops_token=ops_token,
                worker_id=args.worker_id,
                target_root_override=target_root,
                job=job,
            )
            processed += 1
            if args.once:
                break
        except KeyboardInterrupt:
            _log("Interrupted, stopping worker.")
            break
        except Exception as exc:
            _log(f"Worker loop error: {exc}")
            if args.once:
                return 1
            time.sleep(poll_seconds)

    _log(f"Worker exiting. Processed jobs: {processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
