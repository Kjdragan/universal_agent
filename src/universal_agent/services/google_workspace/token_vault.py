from __future__ import annotations

import base64
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .models import GoogleTokenRecord


class TokenCipher(Protocol):
    """Encryption boundary for token storage.

    Production should inject a KMS-backed cipher implementation.
    """

    def encrypt(self, plaintext: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes) -> bytes: ...


class TokenVault(Protocol):
    def upsert(self, record: GoogleTokenRecord) -> None: ...

    def get(self, user_id: str) -> GoogleTokenRecord | None: ...

    def delete(self, user_id: str) -> None: ...


class UnconfiguredTokenCipher:
    """Fail-closed placeholder to prevent accidental plaintext token storage."""

    def encrypt(self, plaintext: bytes) -> bytes:
        del plaintext
        raise RuntimeError("Token cipher is not configured. Inject a KMS-backed TokenCipher implementation.")

    def decrypt(self, ciphertext: bytes) -> bytes:
        del ciphertext
        raise RuntimeError("Token cipher is not configured. Inject a KMS-backed TokenCipher implementation.")


class InMemoryTokenVault:
    def __init__(self) -> None:
        self._records: dict[str, GoogleTokenRecord] = {}

    def upsert(self, record: GoogleTokenRecord) -> None:
        self._records[record.user_id] = record

    def get(self, user_id: str) -> GoogleTokenRecord | None:
        return self._records.get(user_id)

    def delete(self, user_id: str) -> None:
        self._records.pop(user_id, None)


class FileTokenVault:
    """Encrypted JSON file token vault with explicit cipher injection."""

    def __init__(self, storage_path: Path, *, cipher: TokenCipher | None = None) -> None:
        self._storage_path = storage_path
        self._cipher: TokenCipher = cipher or UnconfiguredTokenCipher()

    def upsert(self, record: GoogleTokenRecord) -> None:
        db = self._load_db()
        payload = asdict(record)
        payload["issued_at"] = record.issued_at.isoformat()
        payload["expires_at"] = record.expires_at.isoformat() if record.expires_at else None
        payload["scopes"] = list(record.scopes)

        encrypted = self._cipher.encrypt(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        db[record.user_id] = base64.urlsafe_b64encode(encrypted).decode("ascii")
        self._write_db(db)

    def get(self, user_id: str) -> GoogleTokenRecord | None:
        db = self._load_db()
        raw = db.get(user_id)
        if not isinstance(raw, str) or not raw:
            return None

        encrypted = base64.urlsafe_b64decode(raw.encode("ascii"))
        plaintext = self._cipher.decrypt(encrypted)
        payload = json.loads(plaintext.decode("utf-8"))
        return GoogleTokenRecord(
            user_id=str(payload["user_id"]),
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            scopes=tuple(str(scope) for scope in payload.get("scopes", [])),
            token_type=str(payload.get("token_type", "Bearer") or "Bearer"),
            expires_at=datetime.fromisoformat(payload["expires_at"]) if payload.get("expires_at") else None,
            provider_user_email=str(payload["provider_user_email"]) if payload.get("provider_user_email") else None,
            issued_at=datetime.fromisoformat(payload["issued_at"]),
        )

    def delete(self, user_id: str) -> None:
        db = self._load_db()
        if user_id in db:
            db.pop(user_id)
            self._write_db(db)

    def _load_db(self) -> dict[str, str]:
        if not self._storage_path.exists():
            return {}
        try:
            parsed = json.loads(self._storage_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
        except Exception:
            pass
        return {}

    def _write_db(self, db: dict[str, str]) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_path.write_text(
            json.dumps(db, indent=2, sort_keys=True),
            encoding="utf-8",
        )
