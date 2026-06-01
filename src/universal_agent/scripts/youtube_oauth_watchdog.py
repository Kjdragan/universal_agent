#!/usr/bin/env python3
"""Daily YouTube OAuth watchdog.

Runs once a day (system cron ``youtube_oauth_watchdog``, 7 AM Central) and
answers one question: *will the YouTube digest still be able to talk to
Google tomorrow?*

It performs two independent checks:

1. **Liveness** — actively exchanges the stored refresh token for a fresh
   access token.  A failure (``invalid_grant``) means the token is already
   dead and the digest/poller crons are silently broken right now.
2. **Age** — reads the ``YOUTUBE_OAUTH_REFRESH_TOKEN_MINTED_AT`` stamp and
   computes the token's age.  Because the OAuth app is in "Testing" mode,
   refresh tokens expire ~7 days after minting; once the token is older
   than ``UA_YOUTUBE_OAUTH_WARN_AGE_DAYS`` (default 5) we proactively warn
   so the operator can re-auth before the morning digest breaks.

When either check trips, the watchdog emails the operator a one-tap re-auth
button (a signed link to ``/api/v1/youtube-oauth/start``) so the re-mint can
be kicked off from a phone.  A healthy token sends nothing.

Exit code is always 0 unless the run itself cannot proceed — a watchdog
that fails loudly on a transient network blip would just create noise.

Usage::

    uv run python -m universal_agent.scripts.youtube_oauth_watchdog
    uv run python -m universal_agent.scripts.youtube_oauth_watchdog --force-email
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path
import sys

# Fix python path for local execution.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("youtube_oauth_watchdog")


def _digest_recipient() -> str:
    """Where the warning goes — same recipient the digest emails."""
    return (
        os.getenv("UA_YOUTUBE_DIGEST_EMAIL_TO")
        or os.getenv("UA_DIGEST_EMAIL_TO")
        or os.getenv("UA_OPERATOR_EMAIL")
        or "kevinjdragan@gmail.com"
    ).strip()


def _build_warning_email(state: str, age_days: float | None, detail: str) -> tuple[str, str, str]:
    """Return ``(subject, html, text)`` for the warning email."""
    from universal_agent.services import youtube_oauth_health as yoh

    base = yoh.public_base_url()
    # Generous 14-day TTL so an older warning email's button still works
    # right up until (and a bit past) the expiry window.
    token = yoh.mint_signed_param("start", 14 * 86400)
    start_url = f"{base}{yoh.START_PATH}?t={token}" if token else ""

    if state == "dead":
        subject = "YouTube OAuth token EXPIRED — re-auth needed"
        headline = "Your YouTube OAuth token has expired"
        lead = (
            "The daily YouTube digest and the gold-channel poller cannot reach "
            "Google until you re-authorize. Tap the button below to re-mint the "
            "token from your phone — you'll approve once on Google's consent "
            "screen and you're done."
        )
        color = "#cf222e"
    else:  # expiring
        age_txt = f"{age_days:.1f} days old" if age_days is not None else "approaching its 7-day limit"
        subject = "YouTube OAuth token expiring soon — re-auth recommended"
        headline = "Your YouTube OAuth token is about to expire"
        lead = (
            f"The token is {age_txt}. Google expires it ~7 days after minting "
            "(the OAuth app is still in Testing mode), so re-authorize now to "
            "keep tomorrow's digest from breaking. One tap, one consent screen."
        )
        color = "#bf8700"

    if start_url:
        button = (
            f'<a href="{start_url}" style="display:inline-block;padding:12px 22px;'
            f"background:{color};color:#ffffff;text-decoration:none;font-weight:600;"
            'border-radius:8px;font-size:15px;">🔁 Re-authorize YouTube access</a>'
        )
        button_text = f"Re-authorize: {start_url}"
    else:
        button = (
            '<p style="color:#cf222e;">⚠️ Re-auth button unavailable (signing secret '
            "not configured). Re-mint from a terminal: "
            "<code>uv run python -m universal_agent.scripts.youtube_oauth2_setup</code></p>"
        )
        button_text = (
            "Re-auth button unavailable. Re-mint from a terminal: "
            "uv run python -m universal_agent.scripts.youtube_oauth2_setup"
        )

    html = (
        f'<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;color:#1f2328;">'
        f'<h2 style="margin:0 0 12px;color:{color};">{headline}</h2>'
        f'<p style="font-size:15px;line-height:1.6;margin:0 0 18px;">{lead}</p>'
        f'<p style="margin:0 0 18px;">{button}</p>'
        f'<p style="font-size:12px;color:#6b7280;line-height:1.5;">Diagnostic: {detail}<br>'
        "After you approve, the fresh token is written to production automatically — "
        "the next morning's digest picks it up with no further action.<br>"
        "Permanent fix: publish the OAuth app to “In production” in Google "
        "Cloud Console to remove the 7-day expiry entirely.</p>"
        "</div>"
    )
    text = f"{headline}\n\n{lead}\n\n{button_text}\n\nDiagnostic: {detail}\n"
    return subject, html, text


async def _send_email(subject: str, html: str, text: str, recipient: str) -> bool:
    from universal_agent.services.agentmail_service import AgentMailService
    from universal_agent.services.email_tags import ActionTag, KindTag

    mail = AgentMailService()
    await mail.startup()
    try:
        await mail.send_email(
            to=recipient,
            subject=subject,
            html=html,
            text=text,
            force_send=True,
            require_approval=False,
            action=ActionTag.ACTION,
            kind=KindTag.SYSTEM,
            source="youtube_oauth_watchdog cron",
            related=["service=youtube_daily_digest"],
        )
        return True
    finally:
        await mail.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily YouTube OAuth token watchdog.")
    parser.add_argument(
        "--force-email",
        action="store_true",
        help="Send the warning email even when the token looks healthy (for testing the button).",
    )
    args = parser.parse_args()

    from universal_agent.infisical_loader import initialize_runtime_secrets
    from universal_agent.services import youtube_oauth_health as yoh

    initialize_runtime_secrets()

    client_id = (os.getenv("YOUTUBE_OAUTH_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("YOUTUBE_OAUTH_CLIENT_SECRET") or "").strip()
    refresh_token = (os.getenv(yoh.REFRESH_TOKEN_KEY) or "").strip()

    alive, detail = yoh.test_refresh_token(client_id, client_secret, refresh_token)
    minted_at = yoh.read_minted_at()
    age = yoh.token_age_days(minted_at)
    threshold = yoh.warn_age_days()

    if not alive:
        state = "dead"
    elif age is not None and age >= threshold:
        state = "expiring"
    else:
        state = "healthy"

    age_str = f"{age:.2f}d" if age is not None else "unknown (no minted-at stamp)"
    logger.info(
        "OAuth watchdog: alive=%s state=%s age=%s threshold=%.1fd detail=%s",
        alive, state, age_str, threshold, detail,
    )

    should_email = state in {"dead", "expiring"} or args.force_email
    if not should_email:
        logger.info("Token healthy — no warning email sent.")
        return 0

    # When forcing on a healthy token, present it as the proactive variant.
    effective_state = state if state != "healthy" else "expiring"
    subject, html, text = _build_warning_email(effective_state, age, detail)
    recipient = _digest_recipient()
    logger.info("Sending OAuth %s warning to %s...", effective_state, recipient)
    try:
        ok = asyncio.run(_send_email(subject, html, text, recipient))
        logger.info("Warning email sent: %s", ok)
    except Exception as exc:  # noqa: BLE001 — never crash the watchdog cron
        logger.error("Failed to send OAuth warning email: %s", exc)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
