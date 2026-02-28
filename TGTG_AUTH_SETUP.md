# TGTG Authentication Setup — Step-by-Step Walkthrough

> **Why this doc exists:** The Claude Code sandbox cannot reach `apptoogoodtogo.com`
> due to egress restrictions, so the one-time login must be done from your desktop.
> Everything else (monitoring, alerts, auto-purchase) runs fine on the server once
> these credentials are in place.

---

## What you need before starting

- A desktop or laptop computer (Windows/Mac/Linux — doesn't matter)
- Python 3.9+ installed (`python3 --version` to check)
- Your TGTG account email: **your@email.com**
- Access to that Gmail inbox in a **desktop browser** (Chrome/Firefox/Edge)
- The server `.env` file open and ready to edit (see Step 3)

---

## Step 1 — Install the tgtg package on your desktop

Open a terminal (PowerShell on Windows, Terminal on Mac/Linux) and run:

```bash
pip install tgtg
```

If `pip` isn't found, try `pip3` or `python3 -m pip install tgtg`.

---

## Step 2 — Run the login script

Copy-paste this entire block into your terminal and press Enter:

```bash
python3 -c "
from tgtg import TgtgClient
import json

EMAIL = 'kevinjdragan@gmail.com'

print('Sending magic link to', EMAIL, '...')
print('Do NOT open this email on your phone if you have the TGTG app installed!')
print()

client = TgtgClient(email=EMAIL)
creds = client.get_credentials()

print()
print('=== COPY EVERYTHING BELOW THIS LINE ===')
print(json.dumps(creds, indent=2))
print('=== COPY EVERYTHING ABOVE THIS LINE ===')
"
```

You will see:

```
Sending magic link to kevinjdragan@gmail.com ...
Do NOT open this email on your phone if you have the TGTG app installed!

Check your mailbox on PC to continue...
```

The script is now **waiting**. Leave it running and go to Step 2b.

---

## Step 2b — Click the magic link (IMPORTANT: desktop browser only)

1. Open **Gmail in a desktop browser** (Chrome, Firefox, Edge)
2. Find the email from **Too Good To Go** — subject will be something like
   *"Your magic link"* or *"Log in to Too Good To Go"*
   - Check Spam if it doesn't appear within 60 seconds
3. Click the link inside the email

> **WARNING:** If you have the TGTG mobile app on your phone, do **not** open
> the email on your phone — the app intercepts the magic link and the script
> won't get the tokens. Use a desktop browser (or private/incognito window).

After clicking, switch back to your terminal. Within a few seconds you'll see:

```
Logged in!

=== COPY EVERYTHING BELOW THIS LINE ===
{
  "access_token": "eyJ0eXAiOiJKV1Q...",
  "refresh_token": "eyJ0eXAiOiJKV1Q...",
  "user_id": "12345678",
  "cookie": "datadome=AbCdEf..."
}
=== COPY EVERYTHING ABOVE THIS LINE ===
```

**Copy the entire JSON block.**

---

## Step 3 — Put the credentials into the project `.env`

On the server (or wherever the project runs), create/edit the `.env` file in
the project root (`/home/user/universal_agent/.env`).

Use the `.env.example` file as your template — copy it if `.env` doesn't exist:

```bash
cp .env.example .env
```

Then open `.env` and fill in these four lines using the values from Step 2:

```env
TGTG_EMAIL=kevinjdragan@gmail.com
TGTG_ACCESS_TOKEN=<paste access_token value here>
TGTG_REFRESH_TOKEN=<paste refresh_token value here>
TGTG_COOKIE=<paste cookie value here>
```

> `user_id` is optional — the code derives it automatically.

Save the file.

---

## Step 4 — Set your location and watched items

Still in `.env`, update the location to match where your TGTG stores are:

```env
# Your target area (where the stores are — NOT where the server is)
TGTG_LATITUDE=51.5074
TGTG_LONGITUDE=-0.1278
TGTG_RADIUS=5
```

Replace the lat/lon with your actual location. To find them:
- Google Maps → right-click your location → copy the coordinates shown

If you know specific TGTG item IDs you want to watch:

```env
TGTG_WATCHED_ITEMS=123456,789012
```

Leave it blank to watch all your TGTG favourites.

---

## Step 5 — Verify it works

Back on the server, run:

```bash
uv run python -m src.universal_agent.tgtg.cli status
```

You should see something like:

```
✅ Authenticated as kevinjdragan@gmail.com
   Watching: all favourites (or N items)
   Location: 51.5074, -0.1278 (radius 5 km)
```

If you see an auth error, double-check the tokens were pasted correctly
(no extra spaces, no truncation).

---

## Step 6 — Start the monitor

```bash
uv run python -m src.universal_agent.main
```

Or if using the scheduler/cron setup, the monitor will start automatically
on the next heartbeat cycle.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Too many requests` error on Step 2 | Wait 10–15 minutes and try again — TGTG rate-limits auth requests |
| Email doesn't arrive | Check Spam; try again after 2 minutes |
| Script times out before you click | The polling window is ~2 minutes — just run Step 2 again |
| `TERMS` error | The email isn't linked to a TGTG account — sign up first at toogoodtogo.com |
| Auth works but monitoring fails | Check `TGTG_PROXIES` / Webshare vars in `.env` — the monitor uses a proxy to avoid DataDome rate limits |

---

## Token lifetimes

| Token | Lifetime |
|---|---|
| `access_token` | ~4 hours (auto-refreshed by the monitor) |
| `refresh_token` | Long-lived (weeks/months) |
| `cookie` | Tied to the session; refreshed alongside the access token |

You only need to redo this process if the refresh token expires (rare) or if
you log out of TGTG on all devices.
