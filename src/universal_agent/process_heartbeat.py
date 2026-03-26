"""Process-level heartbeat file for liveness detection.

A dedicated **daemon thread** periodically writes the current Unix timestamp
to a small file on disk.  Because it runs in its own OS thread it is
independent of the asyncio event loop — even if the loop is blocked by a
long-running LLM call, the heartbeat file keeps getting updated.

The companion ``vps_service_watchdog.sh`` checks this file's freshness
instead of (or in addition to) an HTTP health probe.

.. important::

   **This is NOT the UA Heartbeat Service** (``heartbeat_service.py``).

   ============================================  ==========================================
   **Process Heartbeat** (this module)           **UA Heartbeat Service**
   ============================================  ==========================================
   OS-level liveness signal                      Application-level proactive agent scheduler
   Daemon thread, writes file every 10s          Async task, runs agent every ~30 min
   Independent of event loop                     Runs ON the event loop
   Read by ``vps_service_watchdog.sh``           Drives HEARTBEAT.md checks, task dispatch, etc.
   Env prefix: ``UA_PROCESS_HEARTBEAT_*``        Env prefix: ``UA_HEARTBEAT_*`` / ``UA_HB_*``
   ============================================  ==========================================

Env vars
--------
UA_PROCESS_HEARTBEAT_FILE
    Path to the heartbeat file.
    Default: ``/var/lib/universal-agent/heartbeat/gateway.heartbeat``

UA_PROCESS_HEARTBEAT_INTERVAL_SECONDS
    Write interval in seconds.  Default: ``10``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "/var/lib/universal-agent/heartbeat/gateway.heartbeat"
_DEFAULT_INTERVAL = 10

_stop_event: threading.Event | None = None
_thread: threading.Thread | None = None
_current_path: Path | None = None
_current_label = "Process heartbeat"


def _heartbeat_path(*, path_env_var: str, legacy_path_env_var: str | None, default_path: str) -> Path:
    legacy_value = ""
    if legacy_path_env_var:
        legacy_value = os.getenv(legacy_path_env_var, "")
    return Path(
        os.getenv(path_env_var, legacy_value).strip()
        or default_path
    )


def _heartbeat_interval(*, interval_env_var: str, default_interval: float) -> float:
    try:
        raw = os.getenv(interval_env_var)
        if raw is None:
            raw = str(default_interval)
        val = float(raw)
        return max(1.0, val)
    except (ValueError, TypeError):
        return float(default_interval)


def _writer_loop(path: Path, interval: float, stop: threading.Event) -> None:
    """Background thread target — writes timestamp until stopped."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Heartbeat file directory creation failed: %s", exc)
        return

    while not stop.is_set():
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(f"{time.time():.3f}\n", encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass  # best-effort; watchdog will notice stale file
        stop.wait(interval)


def start(
    *,
    path_env_var: str = "UA_PROCESS_HEARTBEAT_FILE",
    legacy_path_env_var: str | None = "UA_HEARTBEAT_FILE",
    interval_env_var: str = "UA_PROCESS_HEARTBEAT_INTERVAL_SECONDS",
    default_path: str = _DEFAULT_PATH,
    default_interval: float = _DEFAULT_INTERVAL,
    thread_name: str = "process-heartbeat",
    log_label: str = "Process heartbeat",
) -> None:
    """Start the heartbeat writer thread (idempotent)."""
    global _stop_event, _thread, _current_path, _current_label
    if _thread is not None and _thread.is_alive():
        return
    path = _heartbeat_path(
        path_env_var=path_env_var,
        legacy_path_env_var=legacy_path_env_var,
        default_path=default_path,
    )
    interval = _heartbeat_interval(interval_env_var=interval_env_var, default_interval=default_interval)
    _current_path = path
    _current_label = log_label
    _stop_event = threading.Event()
    _thread = threading.Thread(
        target=_writer_loop,
        args=(path, interval, _stop_event),
        name=thread_name,
        daemon=True,
    )
    _thread.start()
    logger.info(
        "%s started path=%s interval=%.0fs",
        log_label,
        path,
        interval,
    )


def stop() -> None:
    """Stop the heartbeat writer thread and remove the file."""
    global _stop_event, _thread, _current_path
    if _stop_event is not None:
        _stop_event.set()
    if _thread is not None:
        _thread.join(timeout=5)
        _thread = None
    _stop_event = None
    # Remove file so watchdog sees "no heartbeat" immediately on clean shutdown
    try:
        if _current_path is not None:
            _current_path.unlink(missing_ok=True)
    except OSError:
        pass
    logger.info("%s stopped", _current_label)
    _current_path = None
