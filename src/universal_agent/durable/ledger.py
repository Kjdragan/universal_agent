import hashlib
import sqlite3
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .classification import classify_replay_policy, classify_tool, resolve_tool_policy
from .normalize import hash_normalized_json, normalize_json


@dataclass
class LedgerReceipt:
    tool_call_id: str
    idempotency_key: str
    status: str
    response_ref: Optional[str]
    external_correlation_id: Optional[str]


class ToolCallLedger:
    def __init__(self, conn, workspace_dir: Optional[str] = None):
        self.conn = conn
        self.workspace_dir = workspace_dir
        self.logger = logging.getLogger(__name__)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _json_ref(self, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload
        return json.dumps(payload, default=str)

    def _strip_idempotency_fields(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._strip_idempotency_fields(val)
                for key, val in value.items()
                if key not in ("idempotency_key", "client_request_id")
            }
        if isinstance(value, list):
            return [self._strip_idempotency_fields(item) for item in value]
        if isinstance(value, tuple):
            return [self._strip_idempotency_fields(item) for item in value]
        if isinstance(value, set):
            return sorted(self._strip_idempotency_fields(item) for item in value)
        return value

    def _sanitize_for_idempotency(self, tool_name: str, tool_input: Any) -> Any:
        sanitized = self._strip_idempotency_fields(tool_input)
        if not isinstance(sanitized, dict):
            return sanitized
        upper = tool_name.upper()
        if "COMPOSIO_MULTI_EXECUTE_TOOL" in upper:
            sanitized = dict(sanitized)
            for key in (
                "session_id",
                "current_step",
                "current_step_metric",
                "sync_response_to_workbench",
                "thought",
            ):
                sanitized.pop(key, None)
        return sanitized

    def _compute_scope(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        upper = tool_name.upper()
        if "GMAIL_SEND_EMAIL" in upper:
            to_addr = tool_input.get("to") or tool_input.get("to_email") or ""
            subject = tool_input.get("subject") or ""
            attachment = tool_input.get("attachment") or {}
            attachment_key = attachment.get("s3key") if isinstance(attachment, dict) else ""
            return f"email:{to_addr}:{subject}:{attachment_key}"
        if "UPLOAD" in upper:
            file_path = tool_input.get("path") or tool_input.get("file_path") or ""
            destination = tool_input.get("destination") or tool_input.get("bucket") or ""
            return f"upload:{file_path}:{destination}"
        if "MEMORY" in upper:
            content = tool_input.get("content") or ""
            return f"memory:{hash_normalized_json(content)}"
        return hash_normalized_json(tool_input)

    def _idempotency_key(
        self,
        run_id: str,
        tool_namespace: str,
        tool_name: str,
        normalized_args_hash: str,
        side_effect_scope: str,
        nonce: Optional[str] = None,
    ) -> str:
        parts = [run_id, tool_namespace, tool_name, normalized_args_hash, side_effect_scope]
        if nonce:
            parts.append(nonce)
        raw = "|".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def prepare_tool_call(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        step_id: str,
        tool_name: str,
        tool_namespace: str,
        raw_tool_name: Optional[str] = None,
        tool_input: dict[str, Any],
        metadata: Optional[dict[str, Any]] = None,
        allow_duplicate: bool = False,
        idempotency_nonce: Optional[str] = None,
    ) -> tuple[Optional[LedgerReceipt], str]:
        side_effect_class = classify_tool(tool_name, tool_namespace, metadata)
        replay_policy = classify_replay_policy(tool_name, tool_namespace, metadata)
        policy = resolve_tool_policy(tool_name, tool_namespace)
        policy_matched = 1 if policy else 0
        policy_rule_id = policy.name if policy else None
        request_input = self._strip_idempotency_fields(tool_input)
        idempotency_input = self._sanitize_for_idempotency(tool_name, tool_input)
        normalized_args_hash = hash_normalized_json(idempotency_input)
        side_effect_scope = self._compute_scope(tool_name, idempotency_input)
        nonce = None
        if allow_duplicate:
            nonce = idempotency_nonce or tool_call_id
        elif replay_policy == "RELAUNCH":
            nonce = tool_call_id
        idempotency_key = self._idempotency_key(
            run_id,
            tool_namespace,
            tool_name,
            normalized_args_hash,
            side_effect_scope,
            nonce=nonce,
        )

        if not allow_duplicate:
            existing = self.conn.execute(
                "SELECT * FROM tool_calls WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing and existing["status"] == "succeeded":
                return (
                    LedgerReceipt(
                        tool_call_id=existing["tool_call_id"],
                        idempotency_key=existing["idempotency_key"],
                        status=existing["status"],
                        response_ref=existing["response_ref"],
                        external_correlation_id=existing["external_correlation_id"],
                    ),
                    idempotency_key,
                )

        if not policy_matched and tool_namespace == "composio":
            seen = self.conn.execute(
                """
                SELECT 1 FROM tool_calls
                WHERE tool_namespace = ? AND tool_name = ?
                LIMIT 1
                """,
                (tool_namespace, tool_name),
            ).fetchone()
            if not seen:
                self.logger.warning(
                    "UA_POLICY_UNKNOWN_TOOL tool_name=%s tool_namespace=%s raw_tool_name=%s run_id=%s",
                    tool_name,
                    tool_namespace,
                    raw_tool_name or "",
                    run_id,
                )
                if self.workspace_dir:
                    base_dir = os.path.dirname(self.workspace_dir)
                    audit_dir = os.path.join(base_dir, "policy_audit")
                    os.makedirs(audit_dir, exist_ok=True)
                    payload = {
                        "ts": self._now(),
                        "run_id": run_id,
                        "tool_name": tool_name,
                        "tool_namespace": tool_namespace,
                        "raw_tool_name": raw_tool_name,
                    }
                    audit_path = os.path.join(audit_dir, "unknown_tools.jsonl")
                    try:
                        with open(audit_path, "a", encoding="utf-8") as handle:
                            handle.write(json.dumps(payload, default=str) + "\n")
                    except OSError:
                        self.logger.warning(
                            "policy_unknown_tool_log_failed path=%s run_id=%s",
                            audit_path,
                            run_id,
                        )

        now = self._now()
        try:
            self.conn.execute(
                """
                INSERT INTO tool_calls (
                    tool_call_id,
                    run_id,
                    step_id,
                    created_at,
                    updated_at,
                    raw_tool_name,
                    tool_name,
                    tool_namespace,
                    side_effect_class,
                    replay_policy,
                    policy_matched,
                    policy_rule_id,
                    normalized_args_hash,
                    idempotency_key,
                    status,
                    attempt,
                    request_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_call_id,
                    run_id,
                    step_id,
                    now,
                    now,
                    raw_tool_name,
                    tool_name,
                    tool_namespace,
                    side_effect_class,
                    replay_policy,
                    policy_matched,
                    policy_rule_id,
                    normalized_args_hash,
                    idempotency_key,
                    "prepared",
                    1,
                    normalize_json(request_input),
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            # Handle Race Condition (Unique Constraint): Fetch existing and return as valid receipt
            if "unique" in str(e).lower():
                existing = self.conn.execute(
                    "SELECT * FROM tool_calls WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    return (
                        LedgerReceipt(
                            tool_call_id=existing["tool_call_id"],
                            idempotency_key=existing["idempotency_key"],
                            status=existing["status"],
                            response_ref=existing["response_ref"],
                            external_correlation_id=existing["external_correlation_id"],
                        ),
                        idempotency_key,
                    )
            
            # Handle Missing Parent (Foreign Key Constraint)
            if "foreign key" in str(e).lower():
                self.logger.error(
                    "ledger_insert_failed_fk run_id=%s step_id=%s error=%s",
                    run_id,
                    step_id,
                    str(e),
                )
                raise ValueError(
                    f"Ledger Integrity Error: Parent run/step missing (run_id={run_id}, step_id={step_id})"
                ) from e
            
            raise

        return (None, idempotency_key)

    def mark_running(self, tool_call_id: str) -> None:
        self.conn.execute(
            "UPDATE tool_calls SET status = ?, updated_at = ? WHERE tool_call_id = ?",
            ("running", self._now(), tool_call_id),
        )
        self.conn.commit()

    def mark_succeeded(
        self, tool_call_id: str, response: Any, external_correlation_id: Optional[str] = None
    ) -> None:
        self.conn.execute(
            """
            UPDATE tool_calls
            SET status = ?, updated_at = ?, response_ref = ?, external_correlation_id = ?
            WHERE tool_call_id = ?
            """,
            (
                "succeeded",
                self._now(),
                self._json_ref(response),
                external_correlation_id,
                tool_call_id,
            ),
        )
        self.conn.commit()

    def record_receipt_pending(
        self,
        tool_call_id: str,
        response: Any,
        external_correlation_id: Optional[str] = None,
    ) -> bool:
        row = self.get_tool_call(tool_call_id)
        if not row:
            return False
        self.conn.execute(
            """
            INSERT OR REPLACE INTO tool_receipts (
                tool_call_id,
                run_id,
                tool_name,
                tool_namespace,
                idempotency_key,
                created_at,
                response_ref,
                external_correlation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tool_call_id,
                row["run_id"],
                row["tool_name"],
                row["tool_namespace"],
                row["idempotency_key"],
                self._now(),
                self._json_ref(response),
                external_correlation_id,
            ),
        )
        self.conn.commit()
        return True

    def get_pending_receipt(self, tool_call_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM tool_receipts WHERE tool_call_id = ?",
            (tool_call_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)

    def clear_pending_receipt(self, tool_call_id: str) -> None:
        self.conn.execute(
            "DELETE FROM tool_receipts WHERE tool_call_id = ?",
            (tool_call_id,),
        )
        self.conn.commit()

    def promote_pending_receipt(self, tool_call_id: str) -> bool:
        receipt = self.get_pending_receipt(tool_call_id)
        if not receipt:
            return False
        self.mark_succeeded(
            tool_call_id,
            receipt.get("response_ref") or "",
            receipt.get("external_correlation_id"),
        )
        self.clear_pending_receipt(tool_call_id)
        return True

    def mark_failed(self, tool_call_id: str, error_detail: str) -> None:
        self.conn.execute(
            """
            UPDATE tool_calls
            SET status = ?, updated_at = ?, error_detail = ?
            WHERE tool_call_id = ?
            """,
            ("failed", self._now(), error_detail, tool_call_id),
        )
        self.conn.commit()

    def mark_abandoned_on_resume(
        self, tool_call_id: str, error_detail: str = "abandoned_on_resume"
    ) -> None:
        self.conn.execute(
            """
            UPDATE tool_calls
            SET status = ?, updated_at = ?, error_detail = ?
            WHERE tool_call_id = ?
            """,
            ("abandoned_on_resume", self._now(), error_detail, tool_call_id),
        )
        self.conn.commit()

    def mark_replay_status(self, tool_call_id: str, replay_status: str) -> None:
        self.conn.execute(
            """
            UPDATE tool_calls
            SET replay_status = ?, updated_at = ?
            WHERE tool_call_id = ?
            """,
            (replay_status, self._now(), tool_call_id),
        )
        self.conn.commit()

    def get_receipt_by_idempotency(self, idempotency_key: str) -> Optional[LedgerReceipt]:
        row = self.conn.execute(
            "SELECT * FROM tool_calls WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if not row or row["status"] != "succeeded":
            return None
        return LedgerReceipt(
            tool_call_id=row["tool_call_id"],
            idempotency_key=row["idempotency_key"],
            status=row["status"],
            response_ref=row["response_ref"],
            external_correlation_id=row["external_correlation_id"],
        )

    def get_tool_call(self, tool_call_id: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM tool_calls WHERE tool_call_id = ?",
            (tool_call_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)
