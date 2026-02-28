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
from typing import Any, Optional

from universal_agent.delegation.redis_bus import (
    MISSION_CONSUMER_GROUP,
    MISSION_DLQ_STREAM,
    MISSION_STREAM,
    RedisMissionBus,
)
from universal_agent.delegation.schema import MissionEnvelope, MissionPayload


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(message: str) -> None:
    print(f"[{_now()}] {message}", flush=True)


def _env_flag(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


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


def _redis_url_from_env() -> str:
    explicit = str(os.getenv("UA_REDIS_URL") or "").strip()
    if explicit:
        return explicit
    host = str(os.getenv("UA_REDIS_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = int(str(os.getenv("UA_REDIS_PORT") or "6379").strip() or 6379)
    password = str(os.getenv("REDIS_PASSWORD") or "").strip()
    db = int(str(os.getenv("UA_REDIS_DB") or "0").strip() or 0)
    if password:
        encoded = urllib.parse.quote(password, safe="")
        return f"redis://:{encoded}@{host}:{port}/{db}"
    return f"redis://{host}:{port}/{db}"


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


def _start_endpoint(base_url: str, job_id: str) -> str:
    job_quoted = urllib.parse.quote(job_id, safe="")
    return f"{base_url}/api/v1/ops/tutorials/bootstrap-jobs/{job_quoted}/start"


def _registration_endpoint(base_url: str) -> str:
    return f"{base_url}/api/v1/factory/registrations"


def _factory_id_from_env() -> str:
    return (
        str(os.getenv("UA_FACTORY_ID") or "").strip()
        or str(os.getenv("INFISICAL_MACHINE_IDENTITY_NAME") or "").strip()
        or socket.gethostname()
    )


def _registration_payload(*, factory_id: str, worker_id: str, transport: str) -> dict[str, Any]:
    return {
        "factory_id": factory_id,
        "factory_role": str(os.getenv("FACTORY_ROLE") or "LOCAL_WORKER").strip().upper() or "LOCAL_WORKER",
        "registration_status": "online",
        "capabilities": [
            "tutorial_bootstrap_consumer",
            f"transport:{transport}",
        ],
        "metadata": {
            "worker_id": worker_id,
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "transport": transport,
        },
    }


def _post_registration(
    *,
    base_url: str,
    ops_token: str,
    factory_id: str,
    worker_id: str,
    transport: str,
) -> None:
    payload = _registration_payload(factory_id=factory_id, worker_id=worker_id, transport=transport)
    _request(
        method="POST",
        url=_registration_endpoint(base_url),
        ops_token=ops_token,
        json_payload=payload,
        timeout_seconds=15,
    )


def _mark_job_started(
    *,
    base_url: str,
    ops_token: str,
    worker_id: str,
    job_id: str,
) -> Optional[dict[str, Any]]:
    response = _request(
        method="POST",
        url=_start_endpoint(base_url, job_id),
        ops_token=ops_token,
        json_payload={"worker_id": worker_id},
        timeout_seconds=30,
    )
    if isinstance(response, dict):
        job = response.get("job")
        if isinstance(job, dict):
            return job
    return None


def _process_job(
    *,
    base_url: str,
    ops_token: str,
    worker_id: str,
    target_root_override: str,
    job: dict[str, Any],
) -> dict[str, Any]:
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        _log("Skipping malformed job with missing job_id")
        return {"job_id": "", "status": "failed", "error": "missing_job_id"}

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
    return {
        "job_id": job_id,
        "status": status,
        "error": error,
    }


def _job_from_mission(envelope: MissionEnvelope) -> dict[str, Any]:
    context = envelope.payload.context if isinstance(envelope.payload.context, dict) else {}
    return {
        "job_id": envelope.job_id,
        "tutorial_run_path": str(context.get("tutorial_run_path") or "").strip(),
        "repo_name": str(context.get("repo_name") or "").strip(),
        "target_root": str(context.get("target_root") or "").strip(),
        "python_version": str(context.get("python_version") or "").strip(),
        "timeout_seconds": int(context.get("timeout_seconds") or envelope.timeout_seconds or 900),
    }


def _should_ack_without_retry(error_text: str) -> bool:
    normalized = str(error_text or "")
    return "HTTP 404" in normalized or "HTTP 409" in normalized


def _republish_retry_mission(
    *,
    bus: RedisMissionBus,
    envelope: MissionEnvelope,
    retry_count: int,
) -> str:
    context = envelope.payload.context if isinstance(envelope.payload.context, dict) else {}
    retry_context = dict(context)
    retry_context["_retry_count"] = int(retry_count)
    retry_envelope = MissionEnvelope(
        job_id=envelope.job_id,
        idempotency_key=envelope.idempotency_key,
        priority=int(envelope.priority),
        timeout_seconds=int(envelope.timeout_seconds),
        max_retries=int(envelope.max_retries),
        payload=MissionPayload(
            task=str(envelope.payload.task or ""),
            context=retry_context,
        ),
    )
    return bus.publish_mission(retry_envelope)


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
    parser.add_argument(
        "--transport",
        choices=["auto", "http", "redis"],
        default=os.getenv("UA_TUTORIAL_BOOTSTRAP_TRANSPORT", "auto"),
        help="Worker transport mode: auto, http, redis",
    )
    parser.add_argument(
        "--registration-interval-seconds",
        type=float,
        default=float(os.getenv("UA_FACTORY_REGISTRATION_INTERVAL_SECONDS", "60") or 60),
        help="How often to refresh HQ factory registration heartbeat",
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
    factory_id = _factory_id_from_env()
    registration_interval_seconds = max(10.0, float(args.registration_interval_seconds or 60.0))
    transport = str(args.transport or "auto").strip().lower()
    if transport not in {"auto", "http", "redis"}:
        print("--transport must be auto|http|redis", file=sys.stderr)
        return 2
    if transport == "auto":
        redis_enabled = _env_flag("UA_DELEGATION_REDIS_ENABLED", True)
        transport = "redis" if redis_enabled else "http"

    _log(f"Starting local tutorial bootstrap worker id={args.worker_id}")
    _log(f"Gateway: {base_url}")
    _log(f"Target root: {target_root}")
    _log(f"Transport: {transport}")

    processed = 0
    last_registration_at = 0.0
    stream_name = str(os.getenv("UA_DELEGATION_STREAM_NAME") or MISSION_STREAM).strip() or MISSION_STREAM
    consumer_group = str(os.getenv("UA_DELEGATION_CONSUMER_GROUP") or MISSION_CONSUMER_GROUP).strip() or MISSION_CONSUMER_GROUP
    dlq_stream = str(os.getenv("UA_DELEGATION_DLQ_STREAM") or MISSION_DLQ_STREAM).strip() or MISSION_DLQ_STREAM
    consumer_name = str(os.getenv("UA_DELEGATION_CONSUMER_NAME") or f"worker_{factory_id}").strip() or f"worker_{factory_id}"
    consumer_name = re.sub(r"[^A-Za-z0-9:_-]+", "-", consumer_name)

    mission_bus: Optional[RedisMissionBus] = None
    if transport == "redis":
        try:
            mission_bus = RedisMissionBus.from_url(
                _redis_url_from_env(),
                stream_name=stream_name,
                consumer_group=consumer_group,
                dlq_stream=dlq_stream,
            )
            mission_bus.ensure_group()
            _log(f"Redis mission bus connected stream={stream_name} group={consumer_group} consumer={consumer_name}")
        except Exception as exc:
            _log(f"Failed to initialize Redis mission bus ({exc}); falling back to HTTP queue mode")
            mission_bus = None
            transport = "http"

    while True:
        try:
            now_ts = time.time()
            if (now_ts - last_registration_at) >= registration_interval_seconds:
                try:
                    _post_registration(
                        base_url=base_url,
                        ops_token=ops_token,
                        factory_id=factory_id,
                        worker_id=args.worker_id,
                        transport=transport,
                    )
                    last_registration_at = now_ts
                except Exception as exc:
                    _log(f"Factory registration heartbeat failed: {exc}")

            if transport == "redis" and mission_bus is not None:
                missions = mission_bus.consume(
                    consumer_name=consumer_name,
                    count=1,
                    block_ms=int(poll_seconds * 1000),
                    stream_id=">",
                )
                if not missions:
                    if args.once:
                        _log("No queued Redis missions.")
                        break
                    continue
                for consumed in missions:
                    context = consumed.envelope.payload.context if isinstance(consumed.envelope.payload.context, dict) else {}
                    mission_kind = str(context.get("mission_kind") or "").strip().lower()
                    if mission_kind != "tutorial_bootstrap_repo":
                        _log(f"Skipping unsupported mission kind for job {consumed.envelope.job_id}: {mission_kind or 'unknown'}")
                        mission_bus.ack(consumed.message_id)
                        continue

                    mission_base_url = str(context.get("gateway_url") or "").strip() or base_url
                    try:
                        mission_base_url = _normalize_base_url(mission_base_url)
                    except Exception:
                        mission_base_url = base_url

                    try:
                        _mark_job_started(
                            base_url=mission_base_url,
                            ops_token=ops_token,
                            worker_id=args.worker_id,
                            job_id=consumed.envelope.job_id,
                        )
                        result = _process_job(
                            base_url=mission_base_url,
                            ops_token=ops_token,
                            worker_id=args.worker_id,
                            target_root_override=target_root,
                            job=_job_from_mission(consumed.envelope),
                        )
                        if str(result.get("status") or "").strip().lower() == "completed":
                            mission_bus.ack(consumed.message_id)
                        else:
                            failure_error = str(result.get("error") or "mission_failed")
                            retry_count = int(context.get("_retry_count") or 0) + 1
                            if _should_ack_without_retry(failure_error):
                                mission_bus.ack(consumed.message_id)
                                _log(f"Acknowledged non-retryable mission failure for {consumed.envelope.job_id}: {failure_error}")
                            else:
                                sent_to_dlq = mission_bus.fail_and_maybe_dlq(
                                    consumed=consumed,
                                    failure_error=failure_error,
                                    retry_count=retry_count,
                                )
                                if sent_to_dlq:
                                    _log(f"Mission sent to DLQ job_id={consumed.envelope.job_id} retries={retry_count}")
                                else:
                                    retry_message_id = _republish_retry_mission(
                                        bus=mission_bus,
                                        envelope=consumed.envelope,
                                        retry_count=retry_count,
                                    )
                                    mission_bus.ack(consumed.message_id)
                                    _log(
                                        f"Mission retry republished job_id={consumed.envelope.job_id} "
                                        f"retry={retry_count} message_id={retry_message_id}"
                                    )
                        processed += 1
                    except Exception as exc:
                        failure_error = str(exc)
                        retry_count = int(context.get("_retry_count") or 0) + 1
                        if _should_ack_without_retry(failure_error):
                            mission_bus.ack(consumed.message_id)
                            _log(f"Acknowledged non-retryable mission error for {consumed.envelope.job_id}: {failure_error}")
                        else:
                            try:
                                sent_to_dlq = mission_bus.fail_and_maybe_dlq(
                                    consumed=consumed,
                                    failure_error=failure_error,
                                    retry_count=retry_count,
                                )
                                if sent_to_dlq:
                                    _log(f"Mission sent to DLQ job_id={consumed.envelope.job_id} retries={retry_count}")
                                else:
                                    retry_message_id = _republish_retry_mission(
                                        bus=mission_bus,
                                        envelope=consumed.envelope,
                                        retry_count=retry_count,
                                    )
                                    mission_bus.ack(consumed.message_id)
                                    _log(
                                        f"Mission retry republished after error job_id={consumed.envelope.job_id} "
                                        f"retry={retry_count} message_id={retry_message_id}"
                                    )
                            except Exception as nested_exc:
                                _log(f"Failed handling mission error for {consumed.envelope.job_id}: {nested_exc}")
                    if args.once:
                        break
                if args.once:
                    break
                continue

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
                    _log("No queued HTTP jobs.")
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
