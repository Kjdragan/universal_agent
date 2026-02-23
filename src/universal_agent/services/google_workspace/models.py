from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


class GoogleIntent(StrEnum):
    GMAIL_READ = "gmail.read"
    GMAIL_SEND_REPLY = "gmail.send_reply"
    GMAIL_MODIFY = "gmail.modify"
    CALENDAR_READ_WRITE = "calendar.read_write"
    DRIVE_READ_DOWNLOAD_EXPORT = "drive.read_download_export"
    SHEETS_READ_APPEND = "sheets.read_append"
    DOCS_WRITE = "docs.write"
    LONG_TAIL_GOOGLE = "google.long_tail"
    CROSS_SAAS_ORCHESTRATION = "google.cross_saas"


class ExecutionRoute(StrEnum):
    DIRECT = "direct"
    COMPOSIO = "composio"


@dataclass(frozen=True)
class RoutingDecision:
    intent: GoogleIntent
    route: ExecutionRoute
    reason: str
    fallback_route: ExecutionRoute | None = None


@dataclass(frozen=True)
class GoogleTokenRecord:
    user_id: str
    access_token: str
    refresh_token: str
    scopes: tuple[str, ...]
    token_type: str = "Bearer"
    expires_at: datetime | None = None
    provider_user_email: str | None = None
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def has_scope(self, scope: str) -> bool:
        return scope in set(self.scopes)
