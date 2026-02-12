#!/usr/bin/env python3
"""
Manage dashboard owner credentials for multi-owner authentication.

Default file: ../config/dashboard_owners.json
Format:
{
  "owners": [
    {
      "owner_id": "owner_primary",
      "password_hash": "pbkdf2_sha256$...",
      "active": true,
      "roles": ["admin"]
    }
  ]
}
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

OWNER_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
DEFAULT_ITERATIONS = 310_000


def default_file() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    return repo_root / "config" / "dashboard_owners.json"


def normalize_owner(owner_id: str) -> str:
    normalized = (owner_id or "").strip()
    if not OWNER_PATTERN.match(normalized):
        raise ValueError("owner_id must match ^[A-Za-z0-9._-]{1,64}$")
    return normalized


def pbkdf2_hash(password: str, iterations: int = DEFAULT_ITERATIONS) -> str:
    if not password:
        raise ValueError("password must be non-empty")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${iterations}${salt_b64}${hash_b64}"


def load_records(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"owners": []}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {"owners": []}
    payload = json.loads(raw)
    if isinstance(payload, list):
        return {"owners": payload}
    if isinstance(payload, dict):
        owners = payload.get("owners", [])
        if not isinstance(owners, list):
            owners = []
        return {"owners": owners}
    return {"owners": []}


def save_records(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def cmd_list(path: Path) -> int:
    payload = load_records(path)
    owners = payload.get("owners", [])
    if not owners:
        print(f"No owners configured in {path}")
        return 0
    print(f"Owners in {path}:")
    for row in owners:
        if not isinstance(row, dict):
            continue
        owner_id = str(row.get("owner_id", ""))
        active = bool(row.get("active", True))
        roles = row.get("roles", [])
        print(f"- owner_id={owner_id} active={active} roles={roles}")
    return 0


def cmd_set(path: Path, owner_id: str, password: str, roles: str, active: bool, iterations: int) -> int:
    owner_id = normalize_owner(owner_id)
    payload = load_records(path)
    owners = payload.get("owners", [])
    if not isinstance(owners, list):
        owners = []
    role_values = [item.strip() for item in roles.split(",") if item.strip()] if roles else []

    record = {
        "owner_id": owner_id,
        "password_hash": pbkdf2_hash(password, iterations=iterations),
        "active": bool(active),
        "roles": role_values,
    }

    replaced = False
    next_rows: list[dict[str, Any]] = []
    for row in owners:
        if not isinstance(row, dict):
            continue
        if str(row.get("owner_id", "")).strip() == owner_id:
            next_rows.append(record)
            replaced = True
        else:
            next_rows.append(row)
    if not replaced:
        next_rows.append(record)

    payload["owners"] = next_rows
    save_records(path, payload)
    print(f"{'Updated' if replaced else 'Added'} owner '{owner_id}' in {path}")
    return 0


def cmd_remove(path: Path, owner_id: str) -> int:
    owner_id = normalize_owner(owner_id)
    payload = load_records(path)
    owners = payload.get("owners", [])
    if not isinstance(owners, list):
        owners = []
    next_rows = [
        row for row in owners
        if isinstance(row, dict) and str(row.get("owner_id", "")).strip() != owner_id
    ]
    if len(next_rows) == len(owners):
        print(f"Owner '{owner_id}' not found in {path}")
        return 1
    payload["owners"] = next_rows
    save_records(path, payload)
    print(f"Removed owner '{owner_id}' from {path}")
    return 0


def cmd_hash(password: str, iterations: int) -> int:
    print(pbkdf2_hash(password, iterations=iterations))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage dashboard owner credentials")
    parser.add_argument("--file", default=str(default_file()), help="Path to dashboard_owners.json")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List configured owners")

    set_cmd = sub.add_parser("set", help="Add or update an owner credential")
    set_cmd.add_argument("--owner-id", required=True, help="Owner ID")
    set_cmd.add_argument("--password", required=True, help="Owner password")
    set_cmd.add_argument("--roles", default="admin", help="Comma-separated roles (default: admin)")
    set_cmd.add_argument("--inactive", action="store_true", help="Create/update as inactive")
    set_cmd.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS, help="PBKDF2 iterations")

    rm_cmd = sub.add_parser("remove", help="Remove an owner")
    rm_cmd.add_argument("--owner-id", required=True, help="Owner ID")

    hash_cmd = sub.add_parser("hash", help="Generate a PBKDF2 hash without writing files")
    hash_cmd.add_argument("--password", required=True, help="Password to hash")
    hash_cmd.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS, help="PBKDF2 iterations")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    file_path = Path(args.file).expanduser().resolve()

    if args.command == "list":
        return cmd_list(file_path)
    if args.command == "set":
        return cmd_set(
            file_path,
            owner_id=args.owner_id,
            password=args.password,
            roles=args.roles,
            active=not args.inactive,
            iterations=int(args.iterations),
        )
    if args.command == "remove":
        return cmd_remove(file_path, owner_id=args.owner_id)
    if args.command == "hash":
        return cmd_hash(password=args.password, iterations=int(args.iterations))

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
