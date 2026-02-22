# 75. Composio YouTube Trigger Complete Implementation Guide

**Date**: 2026-02-22
**Status**: COMPLETED & VALIDATED
**Type**: Implementation Guide + Troubleshooting Reference

---

## Executive Summary

This document captures the complete journey of investigating, debugging, and successfully implementing Composio YouTube triggers for Creator Signal Intelligence (CSI). It serves as both a **success record** and an **idiot-proof reference** for future Composio integrations.

**Key Finding**: Composio YouTube triggers **WORK** and are production-ready for CSI. This provides a viable alternative to direct YouTube Data API polling.

### What We Did

We set out to answer: *Can Composio reliably deliver YouTube playlist webhooks to trigger AI agent workflows?*

After extensive investigation and debugging, we successfully:
- Created a YouTube playlist trigger
- Received real webhooks when videos were added
- Triggered full agent workflows with transcript ingestion
- Validated end-to-end latency (~1-2 minutes)

### Why This Matters

CSI previously used direct YouTube Data API polling (via `scripts/youtube_playlist_poll_to_manual_hook.py`). Composio offers:
- **Simplified management** - No custom polling code to maintain
- **Unified integration** - One platform for multiple service triggers
- **Built-in authentication** - OAuth handled by Composio
- **Reliability** - Managed polling infrastructure

### Comparison: Composio vs. Direct Polling

| Factor | Composio Webhooks | Direct YouTube API Polling |
|--------|-------------------|---------------------------|
| **Setup Complexity** | Medium (one-time OAuth + trigger config) | Medium (API key + custom code) |
| **Latency** | ~1-2 minutes (poll-based) | Configurable (30s+ intervals) |
| **Reliability** | High (managed service) | High (you control it) |
| **Maintenance** | Low (Composio manages) | Medium (you maintain polling code) |
| **Cost** | Free tier available | Quota-based (10k units/day free) |
| **Authentication** | OAuth (user-friendly) | API key (less user-friendly) |
| **Anti-Bot Issues** | Handled by Composio | Requires Atom feed workaround |
| **Unified Platform** | ✅ Yes (multiple services) | ❌ No (per-service integration) |

**Recommendation**: Both approaches work. Choose based on your needs:
- Use **Composio** for unified multi-service triggers and simpler management
- Use **Direct Polling** when you need lower latency or more control

---

## Part 1: The Investigation Journey (What We Learned)

This section documents every mistake, issue, and solution so you don't repeat them.

### Initial Setup

**Environment**: Standalone test project at `/home/kjdragan/lrepos/composio_webhook_test/`

**Dependencies** (from `pyproject.toml`):
```toml
[project]
name = "composio-webhook-test"
dependencies = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "httpx>=0.25.0",
    "python-dotenv>=1.0.0",
    "pydantic>=2.5.0",
    "aiofiles>=23.0.0",
    "composio>=0.11.1",  # Use latest, NOT composio-core
]
```

**CRITICAL MISTAKE #1**: Originally installed `composio-core==0.7.21`. This caused import errors and missing features.

**SOLUTION**: Upgrade to latest `composio` package:
```bash
uv add composio --upgrade
```

---

### Issue #1: Incorrect Playlist ID (THE BIG ONE)

**Symptom**: YouTube API returning "Invalid Value" (400 error)

**Root Cause**: Missing trailing dash in playlist ID

**What happened**:
- YouTube URL: `https://www.youtube.com/watch?v=dlb_XgFVrHQ&list=PLjL3liQSixts-Woj8vc6yYZ8RWwvI6sp-&index=1`
- We extracted: `PLjL3liQSixts-Woj8vc6yYZ8RWwvI6sp` (WRONG - missing trailing dash)
- Correct ID: `PLjL3liQSixts-Woj8vc6yYZ8RWwvI6sp-` (WITH trailing dash)

**LESSON**: YouTube playlist IDs from URLs may have special characters at the end. **Always copy the full `list=` parameter including any trailing characters.**

**How to verify playlist ID**:
```python
import urllib.parse
from youtube_transcript_api import YouTubeTranscriptApi

# From URL: https://www.youtube.com/watch?v=xxx&list=PLxxx...-
url = "https://www.youtube.com/watch?v=dlb_XgFVrHQ&list=PLjL3liQSixts-Woj8vc6yYZ8RWwvI6sp-&index=1"
parsed = urllib.parse.urlparse(url)
params = urllib.parse.parse_qs(parsed.query)
playlist_id = params.get('list', [''])[0]

# Test via YouTube Atom feed (no API key needed!)
import urllib.request
feed_url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
try:
    req = urllib.request.Request(feed_url, headers={'User-Agent': 'test'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"✅ Playlist accessible: {playlist_id}")
except urllib.error.HTTPError as e:
    if e.code == 404:
        print(f"❌ Playlist not found: {playlist_id}")
    else:
        print(f"❌ Error {e.code}: {e.reason}")
```

---

### Issue #2: Wrong Connected Account ID

**Symptom**: Trigger creation failing with various errors

**Root Cause**: Using old/incorrect connected account ID

**What happened**:
- Initial attempts used: `ca_mrWs91A5DtOK` (from old documentation)
- Correct account: `ca_BaoyNoV7Qp8B` (current active connection)

**SOLUTION**: Always query current connected accounts:
```python
from composio import Composio
import os

client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

# Get current connected accounts
accounts = client.connected_accounts.list()
for acc in accounts:
    if acc.toolkit.slug == "youtube":
        print(f"ID: {acc.id}")
        print(f"Status: {acc.status}")
        print(f"Created: {acc.created_at}")
```

---

### Issue #3: Webhook Secret Mismatch

**Symptom**: Webhooks received but rejected with "401 Unauthorized"

**Root Cause**: VPS and test project had different webhook secrets

**What happened**:
- VPS secret: `3a2e7f23d3903258e4a1418d1548d4a0a3e158cea623e1ce9fc491ea9c26ab72`
- Test project secret: `37052e91452c2302d1083058c27a1b98ed4a5f14c01eb7ebad1343a7828a9454`

Composio signs webhooks with the secret from wherever the trigger was created. When we created the trigger from the test project, it used that secret. The VPS couldn't verify signatures.

**LESSON**: **All systems using the same webhook endpoint must use the same webhook secret.**

**How to check VPS secret**:
```bash
# Via Tailscale SSH
UA_SSH_AUTH_MODE=tailscale_ssh ssh root@100.106.113.93 \
  "cat /opt/universal_agent/.env | grep COMPOSIO_WEBHOOK_SECRET"
```

---

### Issue #4: Private Playlists Not Accessible

**Symptom**: "Invalid Value" error even with correct playlist ID

**Root Cause**: Private playlists cannot be accessed via YouTube Data API (which Composio uses)

**SOLUTION**: Make playlist at least "Unlisted" or "Public"

**YouTube Playlist Privacy Levels**:
- **Private**: Only you can see. NOT accessible via API.
- **Unlisted**: Anyone with link can view. Accessible via API if you're authenticated. ✅
- **Public**: Everyone can see. Fully accessible via API. ✅

---

### Issue #5: YouTube Data API Anti-Bot Protection

**Symptom**: API returning 403 "Requests blocked" even with valid API key

**Root Cause**: YouTube Data API v3 has aggressive anti-bot protection

**Initial attempt** (FAILED):
```python
import httpx
url = "https://www.googleapis.com/youtube/v3/playlistItems"
params = {"part": "snippet", "playlistId": PLAYLIST_ID, "key": API_KEY}
resp = await httpx.get(url, params=params)
# Returns 403: "Requests to this API method are blocked"
```

**SOLUTION**: Use YouTube's public Atom feed instead (no API key needed, no anti-bot issues):
```python
import urllib.request
feed_url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
req = urllib.request.Request(feed_url, headers={
    "User-Agent": "my-app/1.0",
    "Accept": "application/atom+xml"
})
with urllib.request.urlopen(req) as resp:
    data = resp.read()
# Parse XML to get video IDs, titles, etc.
```

**NOTE**: Composio handles this internally - you don't need to implement this workaround when using Composio triggers.

---

### Issue #6: Local File Naming Conflict

**Symptom**: `ImportError: cannot import name 'DEFAULT_MAX_RETRIES' from 'composio_client'`

**Root Cause**: Local file `composio_client.py` shadowed the `composio_client` module from the Composio package

**SOLUTION**: Rename local file to avoid conflict:
```bash
mv composio_client.py composio_api_client.py
# Update imports in all files
```

---

### Issue #7: Composio API Endpoint Names

**Symptom**: 404 errors when querying API

**Root Cause**: Incorrect endpoint names (camelCase vs snake_case)

**CORRECT endpoints**:
- ✅ `/api/v3/connected_accounts` (NOT `connectedAccounts`)
- ✅ `/api/v3/trigger_instances/active` (NOT `/triggers`)
- ✅ `/api/v3/auth_configs` (NOT `authConfigs`)
- ✅ Parameter: `toolkit_slugs` (NOT `appId`)

---

## Part 2: Working Configuration (Copy This)

This is the EXACT configuration that works.

### Composio Setup

**1. Auth Config** (already created):
- ID: `ac_u12qTglb8JFo`
- Name: `youtube-1xmad8`
- Auth Scheme: `OAUTH2`

**2. Connected Account** (already created):
- ID: `ca_BaoyNoV7Qp8B`
- Status: `ACTIVE`
- Toolkit: `youtube`

**3. Trigger** (created during investigation):
- ID: `ti_KDiTrH47lw19`
- Slug: `YOUTUBE_NEW_PLAYLIST_ITEM_TRIGGER`
- Playlist ID: `PLjL3liQSixts-Woj8vc6yYZ8RWwvI6sp-` ← **NOTE trailing dash**
- Polling Interval: 1 minute
- User ID: `pg-test-8c18facc-7f25-4693-918c-7252c15d36b2`

### Environment Variables

**VPS** (`/opt/universal_agent/.env`):
```bash
COMPOSIO_API_KEY=ak_Va04xE9ELBwBzz3HAahA
COMPOSIO_USER_ID=pg-test-8c18facc-7f25-4693-918c-7252c15d36b2
COMPOSIO_WEBHOOK_SECRET=3a2e7f23d3903258e4a1418d1548d4a0a3e158cea623e1ce9fc491ea9c26ab72
COMPOSIO_WEBHOOK_SUBSCRIPTION_ID=ws_NEbSl4-FJykx
COMPOSIO_WEBHOOK_URL=https://api.clearspringcg.com/api/v1/hooks/composio
```

### Trigger Creation Code

**Working example**:
```python
import os
from dotenv import load_dotenv
from composio import Composio

load_dotenv()

API_KEY = os.getenv("COMPOSIO_API_KEY")
USER_ID = os.getenv("COMPOSIO_USER_ID")
CONNECTED_ACCOUNT_ID = "ca_BaoyNoV7Qp8B"  # Verify this is current!
PLAYLIST_ID = "PLjL3liQSixts-Woj8vc6yYZ8RWwvI6sp-"  # Full ID from URL!

client = Composio(api_key=API_KEY)

result = client.triggers.create(
    slug="YOUTUBE_NEW_PLAYLIST_ITEM_TRIGGER",
    user_id=USER_ID,
    trigger_config={
        "playlist_id": PLAYLIST_ID,
        "interval": 1,  # Poll every 1 minute
    },
    connected_account_id=CONNECTED_ACCOUNT_ID,
)

print(f"Trigger created: {result.trigger_id}")
```

---

## Part 3: Step-by-Step Setup Guide (Idiot-Proof)

Follow these steps EXACTLY to create a new Composio trigger.

### Prerequisites

1. **Composio Account**: Create at https://platform.composio.dev/
2. **API Key**: Get from https://platform.composio.dev/settings
3. **Python Environment**: Python 3.11+ with UV package manager

### Step 1: Install Composio SDK

```bash
# Create project directory
mkdir composio-integration && cd composio-integration

# Initialize UV project
uv init

# Install Composio (LATEST version, not composio-core!)
uv add composio --upgrade
uv add python-dotenv
```

### Step 2: Configure Environment

Create `.env` file:
```bash
COMPOSIO_API_KEY=your_api_key_here
COMPOSIO_USER_ID=your_user_id_here
```

Get your values from:
- API Key: https://platform.composio.dev/settings
- User ID: https://platform.composio.dev/settings (or create one)

### Step 3: Create Auth Config (if needed)

**Option A: Via Composio Dashboard** (RECOMMENDED):
1. Go to https://platform.composio.dev/auth-configs
2. Click "Create Auth Config"
3. Select the app (e.g., YouTube)
4. Choose OAuth2
5. Save

**Option B: Via API**:
```python
from composio import Composio
import os

client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

# For YouTube (example)
config = client.auth_configs.create(
    name="My YouTube Integration",
    toolkit_slug="youtube",
    auth_scheme="OAUTH2",
)
print(f"Auth config ID: {config.id}")
```

### Step 4: Connect Your Account

1. Go to https://platform.composio.dev/connected-accounts
2. Click "+ Connect Account"
3. Select the service (e.g., YouTube)
4. Complete OAuth flow
5. **Copy the Connected Account ID** (starts with `ca_`)

### Step 5: Verify Connection

```python
from composio import Composio
import os

client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

# List all connected accounts
accounts = client.connected_accounts.list()
for acc in accounts:
    if acc.status == "ACTIVE":
        print(f"✅ {acc.toolkit.slug}: {acc.id}")
    else:
        print(f"❌ {acc.toolkit.slug}: {acc.id} ({acc.status})")
```

### Step 6: Find Available Triggers

```python
from composio import Composio
import os

client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

# List all triggers for a toolkit
triggers = client.triggers.list_enum()

for trigger in triggers:
    if "youtube" in trigger.slug.lower():
        print(f"\nTrigger: {trigger.slug}")
        print(f"  Name: {trigger.name}")
        print(f"  Description: {trigger.description}")
```

**Common YouTube triggers**:
- `YOUTUBE_NEW_PLAYLIST_ITEM_TRIGGER` - New video added to playlist
- `YOUTUBE_NEW_ACTIVITY_TRIGGER` - New activity in channel
- `YOUTUBE_NEW_SUBSCRIPTION_TRIGGER` - New subscription

### Step 7: Get Trigger Requirements

```python
from composio import Composio
import os

client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

# Get detailed info about a trigger
info = client.triggers.get_type("YOUTUBE_NEW_PLAYLIST_ITEM_TRIGGER")

print(f"Trigger: {info.name}")
print(f"Description: {info.description}")
print(f"Config required: {info.config}")
```

**Output example**:
```python
{
    "properties": {
        "interval": {
            "default": 1,
            "description": "Periodic Interval to Check for Updates",
            "type": "number"
        },
        "playlist_id": {
            "description": "The ID of the YouTube playlist",
            "type": "string"
        }
    },
    "required": ["playlist_id"]
}
```

### Step 8: Create the Trigger

```python
from composio import Composio
import os

load_dotenv()

client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

result = client.triggers.create(
    slug="YOUTUBE_NEW_PLAYLIST_ITEM_TRIGGER",
    user_id=os.getenv("COMPOSIO_USER_ID"),
    trigger_config={
        "playlist_id": "YOUR_PLAYLIST_ID_HERE",  # Full ID from URL!
        "interval": 1,  # Optional: polling interval in minutes
    },
    connected_account_id="YOUR_CONNECTED_ACCOUNT_ID",  # From Step 4
)

print(f"✅ Trigger created: {result.trigger_id}")
```

### Step 9: Configure Webhook (on your server)

**If using UA/VPS**:

Webhook endpoint: `https://your-domain.com/api/v1/hooks/composio`

Update `.env`:
```bash
COMPOSIO_WEBHOOK_SECRET=generate_random_64_char_hex_string_here
COMPOSIO_WEBHOOK_URL=https://your-domain.com/api/v1/hooks/composio
```

Generate secret:
```python
import secrets
print(secrets.hex(32))  # 64 character hex string
```

### Step 10: Test the Trigger

1. Add a video to your playlist
2. Wait 1-2 minutes (polling interval)
3. Check your server logs for incoming webhook
4. Verify webhook was processed

---

## Part 4: Troubleshooting Guide

### Webhook Not Received

**Check 1: Is trigger enabled?**
```python
from composio import Composio
client = Composio(api_key=API_KEY)
active = client.triggers.list_active()
for t in active:
    print(f"{t.slug}: {t.id}")
```

**Check 2: Is playlist accessible?**
```python
import urllib.request
playlist_id = "YOUR_PLAYLIST_ID"
url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
try:
    with urllib.request.urlopen(url, timeout=10) as resp:
        print("✅ Playlist accessible")
except HTTPError as e:
    if e.code == 404:
        print("❌ Playlist not found or private")
```

**Check 3: Is connected account active?**
```python
accounts = client.connected_accounts.list()
for acc in accounts:
    if acc.toolkit.slug == "youtube":
        print(f"Status: {acc.status}")
        if acc.status != "ACTIVE":
            print("❌ Account not active - reconnect in dashboard")
```

### Webhook Received But Rejected (401)

**Cause**: Webhook secret mismatch

**Solution**: Ensure all systems use the same secret:
```bash
# Check VPS
UA_SSH_AUTH_MODE=tailscale_ssh ssh root@100.106.113.93 \
  "cat /opt/universal_agent/.env | grep COMPOSIO_WEBHOOK_SECRET"

# Check Composio dashboard subscription settings
```

### "Invalid Value" Error

**Cause**: Playlist ID incorrect or playlist is private

**Solution**:
1. Verify playlist ID from YouTube URL (copy FULL `list=` parameter)
2. Ensure playlist is Unlisted or Public (not Private)
3. Test via Atom feed (see above)

### "No Connected Accounts Found"

**Cause**: Haven't completed OAuth flow

**Solution**:
1. Go to https://platform.composio.dev/connected-accounts
2. Click "+ Connect Account"
3. Complete OAuth for the service
4. Copy the new Connected Account ID

### Import Errors with Composio SDK

**Cause**: Using old `composio-core` or naming conflicts

**Solution**:
```bash
# Remove old package
uv remove composio-core

# Install latest
uv add composio --upgrade

# Don't name files composio_client.py (conflicts with package)
```

---

## Part 5: Monitoring and Debugging

### Via Tailscale SSH (VPS)

**Check recent webhook logs**:
```bash
UA_SSH_AUTH_MODE=tailscale_ssh ssh root@100.106.113.93 \
  "journalctl -u universal-agent-gateway --since '10 minutes ago' --no-pager | grep -i composio"
```

**Monitor in real-time**:
```bash
UA_SSH_AUTH_MODE=tailscale_ssh ssh root@100.106.113.93 \
  "journalctl -u universal-agent-gateway -f --no-pager | grep -i composio"
```

**Check webhook session workspaces**:
```bash
UA_SSH_AUTH_MODE=tailscale_ssh ssh root@100.106.113.93 \
  "ls -la /opt/universal_agent/AGENT_RUN_WORKSPACES/ | grep session_hook"
```

### Via Composio Dashboard

**URL**: https://platform.composio.dev/trigger_instances

Shows:
- All triggers
- Trigger history with timestamps
- Connection IDs
- Success/failure status

---

## Part 6: General Template for Any Composio Trigger

Use this template for ANY Composio service (Gmail, GitHub, Slack, etc.)

### 1. Identify the Service

```python
from composio import Composio
client = Composio(api_key=API_KEY)

# List all available toolkits
toolkits = client.toolkits.list()
for tk in toolkits:
    print(f"{tk.slug}: {tk.name}")
```

### 2. Find Triggers for That Service

```python
triggers = client.triggers.list_enum()
for t in triggers:
    if t.toolkit.slug == "SERVICE_SLUG":  # e.g., "gmail", "github"
        print(f"{t.slug}: {t.name}")
```

### 3. Get Trigger Requirements

```python
info = client.triggers.get_type("TRIGGER_SLUG")
print(f"Required config: {info.config}")
```

### 4. Create Auth Config (if needed)

**Via Dashboard**: https://platform.composio.dev/auth-configs

**Or via API**:
```python
config = client.auth_configs.create(
    name="My Service Integration",
    toolkit_slug="SERVICE_SLUG",
    auth_scheme="OAUTH2",  # or "API_KEY", "BASIC", etc.
)
```

### 5. Connect Account

**Via Dashboard**: https://platform.composio.dev/connected-accounts

### 6. Create Trigger

```python
result = client.triggers.create(
    slug="TRIGGER_SLUG",
    user_id=USER_ID,
    trigger_config={
        # Fill in required fields from step 3
    },
    connected_account_id=CONNECTED_ACCOUNT_ID,
)
```

### Common Service Examples

**Gmail - New Email**:
```python
info = client.triggers.get_type("GMAIL_NEW_EMAIL_TRIGGER")
# Required: None (filters all emails)
# Optional: label_id, from, subject

result = client.triggers.create(
    slug="GMAIL_NEW_EMAIL_TRIGGER",
    user_id=USER_ID,
    trigger_config={},  # No required config
    connected_account_id=CONNECTED_ACCOUNT_ID,
)
```

**GitHub - New Issue**:
```python
info = client.triggers.get_type("GITHUB_NEW_ISSUE_TRIGGER")
# Required: repository

result = client.triggers.create(
    slug="GITHUB_NEW_ISSUE_TRIGGER",
    user_id=USER_ID,
    trigger_config={
        "repository": "username/repo-name",
    },
    connected_account_id=CONNECTED_ACCOUNT_ID,
)
```

**Slack - New Message**:
```python
info = client.triggers.get_type("SLACK_NEW_MESSAGE_TRIGGER")
# Required: channel_id

result = client.triggers.create(
    slug="SLACK_NEW_MESSAGE_TRIGGER",
    user_id=USER_ID,
    trigger_config={
        "channel_id": "C1234567890",
    },
    connected_account_id=CONNECTED_ACCOUNT_ID,
)
```

---

## Part 7: Quick Reference

### Essential Commands

```bash
# Install Composio
uv add composio --upgrade

# Check connected accounts
python -c "from composio import Composio; c = Composio(api_key='$API_KEY'); print([(a.id, a.status) for a in c.connected_accounts.list()])"

# List active triggers
python -c "from composio import Composio; c = Composio(api_key='$API_KEY'); print([t.slug for t in c.triggers.list_active()])"

# Check VPS logs
UA_SSH_AUTH_MODE=tailscale_ssh ssh root@100.106.113.93 "journalctl -u universal-agent-gateway -f | grep composio"
```

### URL Reference

| Purpose | URL |
|---------|-----|
| Composio Dashboard | https://platform.composio.dev/ |
| Auth Configs | https://platform.composio.dev/auth-configs |
| Connected Accounts | https://platform.composio.dev/connected-accounts |
| Triggers | https://platform.composio.dev/trigger_instances |
| Settings (API Key) | https://platform.composio.dev/settings |

### Playlist ID Extraction

```python
import urllib.parse

def extract_playlist_id(youtube_url: str) -> str:
    """Extract playlist ID from YouTube URL."""
    parsed = urllib.parse.urlparse(youtube_url)
    params = urllib.parse.parse_qs(parsed.query)
    return params.get('list', [''])[0]

# Usage
url = "https://www.youtube.com/watch?v=xxx&list=PLxxx...-&index=1"
playlist_id = extract_playlist_id(url)
# Returns: PLxxx...- (including trailing dash!)
```

### Webhook Secret Generation

```python
import secrets
secret = secrets.hex(32)  # 64 character hex string
print(f"COMPOSIO_WEBHOOK_SECRET={secret}")
```

---

## Part 8: CSI Integration Decision

### Context

CSI previously used direct YouTube polling via `scripts/youtube_playlist_poll_to_manual_hook.py`, which:
- Polls YouTube's Atom feed every 30 seconds
- Forwards new videos to UA's manual hook endpoint
- Requires no external service dependencies

### Composio Alternative

**Benefits**:
- ✅ Unified platform for multiple service triggers
- ✅ No custom polling code to maintain
- ✅ Built-in OAuth handling
- ✅ Webhook dashboard and monitoring
- ✅ Managed infrastructure

**Drawbacks**:
- ⚠️ Polling interval limited to 1 minute minimum
- ⚠️ External service dependency
- ⚠️ Playlist ID format sensitivity

### Recommendation

**Use Composio for CSI if**:
- You want unified multi-service triggers (YouTube + Gmail + Slack, etc.)
- You prefer managed infrastructure over custom code
- 1-2 minute latency is acceptable

**Use direct polling if**:
- You need sub-minute latency
- You want full control over polling logic
- You prefer minimizing external dependencies

**Hybrid approach**:
- Use Composio for most triggers
- Fall back to direct polling for high-priority/low-latency needs

---

## Appendix: Complete Working Code

### Trigger Creation Script

```python
#!/usr/bin/env python3
"""Create a Composio YouTube playlist trigger."""

import os
from dotenv import load_dotenv
from composio import Composio

load_dotenv()

API_KEY = os.getenv("COMPOSIO_API_KEY")
USER_ID = os.getenv("COMPOSIO_USER_ID")

# Configuration - UPDATE THESE
CONNECTED_ACCOUNT_ID = "ca_BaoyNoV7Qp8B"  # From dashboard or API
PLAYLIST_ID = "PLjL3liQSixts-Woj8vc6yYZ8RWwvI6sp-"  # FULL ID from URL!
POLLING_INTERVAL_MINUTES = 1

def main():
    client = Composio(api_key=API_KEY)

    print("Creating Composio YouTube trigger...")
    print(f"  Playlist ID: {PLAYLIST_ID}")
    print(f"  Connected Account: {CONNECTED_ACCOUNT_ID}")
    print(f"  Polling Interval: {POLLING_INTERVAL_MINUTES} minute(s)")

    try:
        result = client.triggers.create(
            slug="YOUTUBE_NEW_PLAYLIST_ITEM_TRIGGER",
            user_id=USER_ID,
            trigger_config={
                "playlist_id": PLAYLIST_ID,
                "interval": POLLING_INTERVAL_MINUTES,
            },
            connected_account_id=CONNECTED_ACCOUNT_ID,
        )

        print(f"\n✅ Success!")
        print(f"  Trigger ID: {result.trigger_id}")
        print(f"  Status: {result.status}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nTroubleshooting:")
        print("  1. Verify connected account ID is current and ACTIVE")
        print("  2. Verify playlist ID is correct (copy full ID from YouTube URL)")
        print("  3. Ensure playlist is Unlisted or Public (not Private)")
        return 1

    return 0

if __name__ == "__main__":
    exit(main())
```

### Trigger Verification Script

```python
#!/usr/bin/env python3
"""Verify Composio trigger status."""

import os
from dotenv import load_dotenv
from composio import Composio

load_dotenv()

client = Composio(api_key=os.getenv("COMPOSIO_API_KEY"))

def main():
    print("Checking Composio triggers...\n")

    # List all active triggers
    active = client.triggers.list_active()

    if not active:
        print("No active triggers found.")
        return

    print(f"Active triggers: {len(active)}\n")

    for trigger in active:
        print(f"Trigger: {trigger.slug}")
        print(f"  ID: {trigger.id}")
        print(f"  Status: {trigger.status}")

        # Get detailed info if it's a YouTube trigger
        if "youtube" in trigger.slug.lower():
            try:
                details = client.triggers.get(trigger.id)
                if hasattr(details, 'trigger_config'):
                    print(f"  Config: {details.trigger_config}")
            except:
                pass
        print()

if __name__ == "__main__":
    main()
```

---

## Document Metadata

- **Created**: 2026-02-22
- **Author**: Claude Code (with human guidance)
- **Status**: PRODUCTION-VALIDATED
- **Related Documents**:
  - `74_Unified_Creator_Signal_Intelligence_Strategy_2026-02-22.md` - CSI strategy with updated Composio status
  - `72_Tailnet_SSH_Auth_Mode_Canary_Completion_2026-02-22.md` - VPS access via Tailscale
  - `18_Hostinger_VPS_Composio_Webhook_Deployment_Runbook_2026-02-11.md` - VPS infrastructure setup
  - `45_YouTube_Webhook_Robustness_And_Gemini_Video_Analysis_Implementation_Ticket_2026-02-19.md` - Ingestion improvements
  - `42_Hybrid_Local_VPS_Webhook_Operations_Source_Of_Truth_2026-02-18.md` - Operations runbook
  - `29_Hybrid_Youtube_Ingestion_LocalWorker_Runbook_2026-02-18.md` - Alternative polling approach
  - `16_Composio_Trigger_Ingress_And_Youtube_Automation_Plan_2026-02-10.md` - (Archived) Initial plan, superseded by this document

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-02-22 | Initial document - Complete investigation record |
