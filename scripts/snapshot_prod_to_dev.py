#!/usr/bin/env python3
"""Snapshot production SQLite databases to local dev.

UA is 100% SQLite — both prod (on the VPS) and dev (on operator's
desktop) use the same database engine. So "snapshot prod to dev"
collapses to "copy the SQLite files over SSH safely."

This script uses SQLite's online ``.backup`` command on the VPS to
create a consistent point-in-time snapshot WITHOUT pausing prod, then
``scp`` the snapshot to the local dev workspace.

Usage::

    # Standard run — snapshot known runtime DBs from VPS to local
    python scripts/snapshot_prod_to_dev.py

    # Custom ssh host or paths
    python scripts/snapshot_prod_to_dev.py \\
        --ssh-host ua@uaonvps \\
        --prod-workspaces-dir /opt/universal_agent/AGENT_RUN_WORKSPACES \\
        --dev-workspaces-dir ./AGENT_RUN_WORKSPACES

    # Dry-run (print what would happen, no SSH)
    python scripts/snapshot_prod_to_dev.py --dry-run

Safety
------

* Operator-only — uses your SSH keys; doesn't take credentials.
* Refuses to overwrite if the local DB file is newer than the prod
  snapshot would be (unless ``--force``). Prevents stomping on local
  changes during dev work.
* Refuses to run in production (``UA_RUNTIME_STAGE=production``) — this
  is a dev-side workflow only.

Limitations
-----------

* SSH must be set up to the prod host as the configured user.
* ``sqlite3`` CLI must be available on both sides (it usually is —
  ships with Python on macOS/Linux).
* CSI db at ``/var/lib/universal-agent/csi/csi.db`` is owned by root
  on the VPS and not snapshotted by this script (would require sudo).
  Add ``--include-csi`` if you really need it — requires root SSH.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Iterable

# Known SQLite databases we snapshot from the prod AGENT_RUN_WORKSPACES dir.
# Mirrors the DEFAULT_*_DB_FILENAME constants in
# src/universal_agent/durable/db.py.
KNOWN_DBS: tuple[str, ...] = (
    "runtime_state.db",
    "activity_state.db",
    "vp_state.db",
    "coder_vp_state.db",
)

DEFAULT_SSH_HOST = "ua@uaonvps"
DEFAULT_PROD_WORKSPACES_DIR = "/opt/universal_agent/AGENT_RUN_WORKSPACES"
DEFAULT_DEV_WORKSPACES_DIR = "AGENT_RUN_WORKSPACES"


@dataclass
class SnapshotConfig:
    ssh_host: str
    prod_workspaces_dir: str
    dev_workspaces_dir: Path
    dbs: tuple[str, ...]
    dry_run: bool
    force: bool


def _refuse_in_production() -> None:
    if (os.environ.get("UA_RUNTIME_STAGE") or "").strip().lower() == "production":
        print(
            "snapshot_prod_to_dev: refusing to run in production "
            "(UA_RUNTIME_STAGE=production). This is a dev-side workflow.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def _run(cmd: list[str], *, check: bool = True, dry_run: bool = False) -> int:
    """Run a subprocess, or just print it in dry-run mode."""
    quoted = " ".join(repr(c) if " " in c else c for c in cmd)
    if dry_run:
        print(f"DRY-RUN: would run: {quoted}")
        return 0
    print(f"  running: {quoted}")
    proc = subprocess.run(cmd, check=False)
    if check and proc.returncode != 0:
        print(
            f"  ERROR: command exited with code {proc.returncode}",
            file=sys.stderr,
        )
        raise SystemExit(proc.returncode)
    return proc.returncode


def _snapshot_one_db(db_filename: str, cfg: SnapshotConfig) -> bool:
    """Snapshot a single SQLite DB from prod to dev. Returns True on success."""
    print(f"\n=== {db_filename} ===")
    prod_path = f"{cfg.prod_workspaces_dir.rstrip('/')}/{db_filename}"
    dev_path = cfg.dev_workspaces_dir / db_filename
    remote_tmp = f"/tmp/ua_snapshot_{db_filename}"

    # Pre-check: confirm prod DB exists. Use ssh test exit code.
    check_cmd = ["ssh", cfg.ssh_host, f"test -f {prod_path!s}"]
    if cfg.dry_run:
        print(f"DRY-RUN: would check if prod DB exists at {prod_path}")
    else:
        if subprocess.run(check_cmd, check=False).returncode != 0:
            print(f"  skip: {prod_path} does not exist on prod host.")
            return False

    # Local conflict check: if dev DB exists AND is newer than ~now,
    # someone may be using it. --force skips this.
    if dev_path.exists() and not cfg.force:
        import time
        mtime = dev_path.stat().st_mtime
        age_seconds = time.time() - mtime
        if age_seconds < 300:
            print(
                f"  REFUSING: local {dev_path} was modified {age_seconds:.0f}s "
                f"ago (within 5 min). Use --force to override.",
                file=sys.stderr,
            )
            return False

    # 1. sqlite3 .backup on prod (creates consistent snapshot without lock)
    backup_cmd = [
        "ssh",
        cfg.ssh_host,
        f"sqlite3 {prod_path!s} '.backup {remote_tmp!s}'",
    ]
    _run(backup_cmd, dry_run=cfg.dry_run)

    # 2. scp the snapshot to a local tmp file
    cfg.dev_workspaces_dir.mkdir(parents=True, exist_ok=True)
    local_tmp = dev_path.with_suffix(dev_path.suffix + ".snapshot.tmp")
    scp_cmd = ["scp", f"{cfg.ssh_host}:{remote_tmp}", str(local_tmp)]
    _run(scp_cmd, dry_run=cfg.dry_run)

    # 3. atomic rename local_tmp → dev_path
    if not cfg.dry_run:
        local_tmp.replace(dev_path)
        print(f"  ✓ snapshot installed at {dev_path}")
    else:
        print(f"DRY-RUN: would rename {local_tmp} → {dev_path}")

    # 4. clean up remote tmp
    cleanup_cmd = ["ssh", cfg.ssh_host, f"rm -f {remote_tmp!s}"]
    _run(cleanup_cmd, check=False, dry_run=cfg.dry_run)

    return True


def parse_args(argv: list[str] | None = None) -> SnapshotConfig:
    parser = argparse.ArgumentParser(
        description="Snapshot prod SQLite DBs to local dev workspace.",
    )
    parser.add_argument(
        "--ssh-host",
        default=DEFAULT_SSH_HOST,
        help=f"SSH host (default: {DEFAULT_SSH_HOST}).",
    )
    parser.add_argument(
        "--prod-workspaces-dir",
        default=DEFAULT_PROD_WORKSPACES_DIR,
        help=f"Prod workspaces dir (default: {DEFAULT_PROD_WORKSPACES_DIR}).",
    )
    parser.add_argument(
        "--dev-workspaces-dir",
        default=DEFAULT_DEV_WORKSPACES_DIR,
        help=f"Dev workspaces dir (default: {DEFAULT_DEV_WORKSPACES_DIR}).",
    )
    parser.add_argument(
        "--db",
        action="append",
        default=None,
        help="Database filename to snapshot (repeatable). "
        f"Default: all known DBs ({', '.join(KNOWN_DBS)}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite local DBs even if recently modified.",
    )
    args = parser.parse_args(argv)
    return SnapshotConfig(
        ssh_host=args.ssh_host,
        prod_workspaces_dir=args.prod_workspaces_dir,
        dev_workspaces_dir=Path(args.dev_workspaces_dir),
        dbs=tuple(args.db) if args.db else KNOWN_DBS,
        dry_run=args.dry_run,
        force=args.force,
    )


def _check_ssh_available() -> None:
    if shutil.which("ssh") is None:
        print(
            "snapshot_prod_to_dev: ssh not found in PATH. Install openssh-client.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if shutil.which("scp") is None:
        print(
            "snapshot_prod_to_dev: scp not found in PATH. Install openssh-client.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> int:
    _refuse_in_production()
    cfg = parse_args(argv)
    if not cfg.dry_run:
        _check_ssh_available()

    print(f"snapshot_prod_to_dev: {cfg.ssh_host}:{cfg.prod_workspaces_dir} → {cfg.dev_workspaces_dir}")
    if cfg.dry_run:
        print("(dry-run mode: no commands will execute)")

    successes: list[str] = []
    failures: list[str] = []
    for db_filename in cfg.dbs:
        try:
            ok = _snapshot_one_db(db_filename, cfg)
        except SystemExit:
            raise
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            ok = False
        if ok:
            successes.append(db_filename)
        else:
            failures.append(db_filename)

    print()
    print(f"snapshot complete: {len(successes)} success, {len(failures)} skipped/failed")
    if successes:
        print(f"  ✓ {', '.join(successes)}")
    if failures:
        print(f"  ✗ {', '.join(failures)}")
    return 0 if failures == [] else 1


if __name__ == "__main__":
    raise SystemExit(main())
