---
name: captcha-solver
description: Use this skill ONLY WHEN access to a webpage is blocked by a CAPTCHA (e.g. Cloudflare Turnstile, reCAPTCHA, hCaptcha). Do not use this preemptively. Bypasses CAPTCHA protection by using an intelligent solver extension via Playwright. VERY IMPORTANT: We only have 100 free requests per day, so you MUST BE EXTREMELY JUDICIOUS. Only use this skill when normal `agent-browser` or `playwright-cli` gets blocked or asks you to solve a CAPTCHA. If the VPS IP itself is blocked BEFORE the CAPTCHA even appears, chain with the `residential-proxy` skill first.
---

# Captcha Solver Skill

This skill grants agents the ability to bypass advanced CAPTCHA protections. Because our usage limit is capped at **100 attempts per day**, you **MUST NOT** use this tool for every scrape. It should be employed strictly as a fallback.

When standard navigation encounters a CAPTCHA block like Cloudflare Turnstile, reCAPTCHA, or hCaptcha, you can use the provided Python script to spawn a persistent context that automates the check natively, waits for the resolution, and then returns the HTML page content and an active `cookies.json` session that you can use in subsequent actions.

## Usage Guide

The solver is located in this directory's `scripts/` folder. It uses `uv run` to ensure project dependencies (like `playwright`) are loaded correctly.

### Basic usage (no proxy)

```bash
uv run .agents/skills/captcha-solver/scripts/solve_with_nopecha.py <URL> --out-html /tmp/bypassed.html --out-cookies /tmp/bypassed_cookies.json --wait-time 15
```

- `<URL>`: the target URL that is protected.
- `--out-html`: path where the script will dump the page's HTML body once the wait period has passed. Check this file to verify the bypass was successful.
- `--out-cookies`: path to save the authenticated Playwright `storageState`. 
- `--wait-time`: The number of seconds the script will stay open locally after loading the page, allowing the extension time to execute and bypass. Default is 15. If the site has a very slow challenge, you can increase this.

### With residential proxy (for VPS/datacenter IP blocks)

If the VPS IP is blocked before you even see a CAPTCHA (e.g., Cloudflare "Access Denied" or 403/503), chain with the `residential-proxy` skill:

```bash
PROXY_URL=$(uv run .agents/skills/residential-proxy/scripts/get_proxy_url.py)
uv run .agents/skills/captcha-solver/scripts/solve_with_nopecha.py \
  "<URL>" \
  --proxy "$PROXY_URL" \
  --out-html /tmp/bypassed.html \
  --out-cookies /tmp/bypassed_cookies.json \
  --wait-time 20
```

### Integration with `agent-browser`

If you need to resume scraping interactively after solving the CAPTCHA, you can inject the exported cookies into an `agent-browser` session.

1. Generate the cookie session:
   ```bash
   uv run .agents/skills/captcha-solver/scripts/solve_with_nopecha.py "https://protected-site.com" --out-cookies /tmp/auth.json
   ```
2. In subsequent commands, load the state with `agent-browser`:
   ```bash
   agent-browser state load /tmp/auth.json
   agent-browser open "https://protected-site.com"
   agent-browser snapshot -i
   ```

### Important rules
1. **Fallback only:** Try normal scraping first. Only when you receive a snapshot confirming a CAPTCHA challenge are you permitted to invoke this specific script.
2. **Quota limits:** Because we are restricted to 100 uses per day, verify that your inputs are correct before firing off the solver command. Do not put this script within an infinite retry loop.
3. **IP block vs CAPTCHA:** If you get a 403/503 "Access Denied" before any CAPTCHA is shown, that's an IP block — use the `residential-proxy` skill's `--proxy` flag. If you see a CAPTCHA challenge, that's when this skill does its work.
4. If `--headless=new` fails, the server uses a display-less runner and might require `xvfb-run`. The Python script tries to use `--headless=new` which allows extensions while omitting a UI, so it should generally run smoothly in CI spaces.
