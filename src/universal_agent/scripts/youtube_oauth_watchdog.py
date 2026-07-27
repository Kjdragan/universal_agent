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


def _build_warning_email(
    state: str,
    age_days: float | None,
    detail: str,
    *,
    observed_title: str = "",
    expected_title: str = "",
) -> tuple[str, str, str]:
    """Return ``(subject, html, text)`` for the warning email."""
    import html as _html

    from universal_agent.services import youtube_oauth_health as yoh

    base = yoh.public_base_url()
    # Generous 14-day TTL so an older warning email's button still works
    # right up until (and a bit past) the expiry window.
    token = yoh.mint_signed_param("start", 14 * 86400)
    start_url = f"{base}{yoh.START_PATH}?t={token}" if token else ""

    if state == "wrong_channel":
        # The failure liveness can't see: the token WORKS but acts as the
        # wrong channel, so every playlist read 404s while the watchdog
        # keeps saying "healthy". Ate two days of digests on 2026-07-25/26.
        ob = _html.escape(observed_title or "an unexpected channel")
        ex = _html.escape(expected_title or "the channel that owns the day-Digest playlists")
        subject = "YouTube OAuth token is for the WRONG channel — re-auth needed"
        headline = "Your YouTube token belongs to the wrong channel"
        lead = (
            f"The token is alive, but it acts as <b>{ob}</b> — not <b>{ex}</b>, "
            "which owns the digest playlists. Every playlist read is failing "
            "with playlistNotFound, so digests are silently dead. Re-authorize "
            f"and at Google's account picker choose <b>{ex}</b>."
        )
        color = "#cf222e"
    elif state == "dead":
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

    access_token, detail = yoh.refresh_access_token(client_id, client_secret, refresh_token)
    alive = access_token is not None or detail.startswith("inconclusive")
    minted_at = yoh.read_minted_at()
    age = yoh.token_age_days(minted_at)
    threshold = yoh.warn_age_days()

    # CHANNEL IDENTITY — the check liveness cannot make. A token for the
    # wrong channel (wrong profile picked at Google's account chooser)
    # passes liveness forever while every playlist read 404s: exactly how
    # the 2026-07-24 re-auth silently killed two days of digests. Expected
    # identity is adopted from the current token on first run (trust on
    # first use) and asserted every day after.
    observed_id, observed_title = "", ""
    expected = (os.getenv(yoh.EXPECTED_CHANNEL_KEY) or "").strip()
    expected_title = (os.getenv(yoh.EXPECTED_CHANNEL_TITLE_KEY) or "").strip()
    wrong_channel = False
    if access_token:
        observed = yoh.fetch_token_channel(access_token)
        if observed is None:
            logger.warning("Channel identity check inconclusive — skipping (never alarm on a fetch error).")
        else:
            observed_id, observed_title = observed
            if not expected and observed_id:
                from universal_agent.infisical_loader import upsert_infisical_secret

                ok_id = upsert_infisical_secret(yoh.EXPECTED_CHANNEL_KEY, observed_id)
                upsert_infisical_secret(yoh.EXPECTED_CHANNEL_TITLE_KEY, observed_title)
                logger.info(
                    "Adopted expected channel (trust on first use): %s (%s) saved=%s",
                    observed_title, observed_id, ok_id,
                )
            elif expected and observed_id != expected:
                wrong_channel = True

    if not alive:
        state = "dead"
    elif wrong_channel:
        state = "wrong_channel"
    elif age is not None and age >= threshold:
        state = "expiring"
    else:
        state = "healthy"

    age_str = f"{age:.2f}d" if age is not None else "unknown (no minted-at stamp)"
    logger.info(
        "OAuth watchdog: alive=%s state=%s age=%s threshold=%.1fd channel=%s(%s) expected=%s detail=%s",
        alive, state, age_str, threshold, observed_title or "?", observed_id or "?",
        expected or "unset", detail,
    )

    should_email = state in {"dead", "expiring", "wrong_channel"} or args.force_email
    if not should_email:
        logger.info("Token healthy — no warning email sent.")
        return 0

    # When forcing on a healthy token, present it as the proactive variant.
    effective_state = state if state != "healthy" else "expiring"
    if state == "wrong_channel":
        detail = (
            f"token acts as '{observed_title}' ({observed_id}); "
            f"expected '{expected_title}' ({expected})"
        )
    subject, html, text = _build_warning_email(
        effective_state, age, detail,
        observed_title=observed_title, expected_title=expected_title,
    )
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
