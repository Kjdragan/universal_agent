from __future__ import annotations

OPENID_SCOPE = "openid"
EMAIL_SCOPE = "email"
PROFILE_SCOPE = "profile"

GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SPREADSHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

DOCUMENTS_SCOPE = "https://www.googleapis.com/auth/documents"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

WAVE_1_SCOPES: tuple[str, ...] = (
    OPENID_SCOPE,
    EMAIL_SCOPE,
    PROFILE_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_SEND_SCOPE,
    CALENDAR_EVENTS_SCOPE,
    DRIVE_READONLY_SCOPE,
    SPREADSHEETS_SCOPE,
)

WAVE_2_SCOPES: tuple[str, ...] = (DOCUMENTS_SCOPE,)
WAVE_3_SCOPES: tuple[str, ...] = ()

DEFERRED_SCOPES: tuple[str, ...] = (
    DRIVE_FILE_SCOPE,
    DOCUMENTS_SCOPE,
)


def missing_wave_1_scopes(granted_scopes: set[str]) -> list[str]:
    granted = {scope.strip() for scope in granted_scopes if scope.strip()}
    return [scope for scope in WAVE_1_SCOPES if scope not in granted]
