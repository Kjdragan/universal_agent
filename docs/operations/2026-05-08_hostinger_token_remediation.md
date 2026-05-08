# 2026-05-08 — Hostinger API Token Leak Remediation

> **Status:** Action items for operator (Kevin). Code/Infisical side is done; the
> remaining work is verifying revocation in Hostinger and (optionally) deciding
> whether to rewrite git history.

---

## 1. What happened

A literal Hostinger API token was committed to `.mcp.json` line 33 inside the
`hostinger-mcp` server config, captured in repo history from
**2026-02-19 (commit `911ce4cc`)** through **2026-05-08 (commit `bcc5fd4c`)** —
roughly **78 days**. The literal value was visible to anyone who could clone or
fetch the repository during that window.

| Event | Commit | Date | Detail |
|---|---|---|---|
| Token introduced | `911ce4cc` | 2026-02-19 | "Version shared memory snapshot and local MCP config files" |
| Periodic re-write through deploy auto-commits | `9526dbfc` (and others) | 2026-04-23 | `chore: deployment auto-commit via /ship` |
| Sanitization (placeholder) | `bcc5fd4c` | 2026-05-08 | `.mcp.json:33` → `${HOSTINGER_API_TOKEN}` and migrated to Infisical (`production / HOSTINGER_API_TOKEN`) |

`HEAD` on `main` no longer contains the literal as of `bcc5fd4c`. But every
prior commit on `main`, `develop`, `feature/latest2`, and any of the **119
refs** that include those commits in their ancestry still contain it.

---

## 2. The threat model (read this before deciding what to do)

Treat the leaked token as **public** until you've revoked it. Specifically:

1. **Anyone who cloned the repo at any point in those 78 days** has the token
   in their local `.git/`. That includes any CI runner, any backup snapshot,
   any forks (whether public or private), and any laptop that pulled the repo.
2. **GitHub itself indexes commit content.** Even after we sanitized `HEAD`,
   the literal exists at commit `911ce4cc..bcc5fd4c^` in GitHub's storage.
   GitHub's secret-scanning may have flagged it; deleted PR refs and PR
   comments can also retain it.
3. **GitHub caches dangling commits for ~90 days** in some scenarios (after
   force-push, after PR closure). Even a history rewrite (§4) does not
   guarantee the literal is gone from GitHub's cache immediately.
4. **The `master` and `develop` branches' historical refs are mirrored to any
   downstream system** that pulls from us — local checkouts on the desktop,
   the production VPS at `/opt/universal_agent/.git/`, GitHub Actions runners
   that did `actions/checkout` during deploy, any forked repo or PR.

What this means in plain terms: **history rewriting cannot fully eliminate the
leaked value.** The only fix that fully nullifies the leak is revoking the
token at Hostinger, after which the leaked literal is just a useless string.

---

## 3. ✅ DO THIS — revoke the old token at Hostinger (highest priority)

**Confirm the old token is REVOKED, not just superseded.** Most platforms
(Hostinger included) let you have multiple active API tokens. "Generating a
new one" does NOT automatically invalidate the old one.

Steps in the Hostinger control panel:

1. Log in to https://hpanel.hostinger.com/.
2. Open API Token management (Account → API → Manage tokens, or similar — the
   exact path depends on your account's UI version).
3. **Find the token whose value starts `ei5J`** (you can compare the first few
   characters; Hostinger usually shows a prefix even if it hides the full
   value).
4. **DELETE / REVOKE that token.** Do not just rename it or mark it
   inactive — fully revoke.
5. (Already done) Confirm the new token's value is in Infisical at
   `production / HOSTINGER_API_TOKEN`. The user already updated this.

### How to verify revocation worked

```bash
# Curl the Hostinger API with the OLD token; should return 401/403
curl -i \
    -H "Authorization: Bearer ei5Jd5yVKFtX16mJyIOoghYDMV2CFeE7VaIm9qDM54249443" \
    https://developers.hostinger.com/api/vps/v1/virtual-machines
```

If the response is `200 OK` or returns data, the old token is **still
active** — go back to the Hostinger UI and revoke it. Repeat the curl until
you get a `401 Unauthorized` (or `403 Forbidden`).

**Once you've confirmed `401/403`, the leak is neutralized.** The literal in
git history is now a dead string. You can stop here unless you have a specific
compliance requirement to scrub the value from history.

---

## 4. ⚠️ OPTIONAL — rewrite git history to scrub the literal

Only do this if you have a reason beyond revocation (compliance audit, public
fork concerns, etc.). Rewriting history is **destructive and operationally
expensive**:

- Every collaborator must delete and re-clone (rebasing won't be enough).
- Every CI runner / VPS / dev machine that has a checkout must `git fetch
  --all && git reset --hard origin/<branch>`.
- The 119 refs that contain the bad commits all need to be rewritten or
  deleted.
- GitHub caches dangling commits for ~90 days after force-push.
- Forks (yours or anyone else's) keep their copy of the bad commits unless
  manually rewritten.

If you decide to proceed:

### 4.1 Pre-flight

```bash
# Verify revocation FIRST (per §3). Don't rewrite history while the
# token is still live; you'll create a window where the literal is
# valid AND scattered across multiple branches that are mid-rewrite.

# Take a backup of the current repo state:
cd /tmp
git clone --mirror https://github.com/Kjdragan/universal_agent universal_agent.backup-2026-05-08.git
ls -la universal_agent.backup-2026-05-08.git
```

### 4.2 Use `git-filter-repo` (the modern replacement for filter-branch)

```bash
# 1. Install git-filter-repo if you don't have it:
pip install --user git-filter-repo

# 2. Clone fresh (filter-repo refuses to operate on a non-fresh clone):
cd /tmp
git clone https://github.com/Kjdragan/universal_agent universal_agent.scrubbed
cd universal_agent.scrubbed

# 3. Build a replacement file with the literal → placeholder mapping:
cat > /tmp/replace.txt <<'EOF'
ei5Jd5yVKFtX16mJyIOoghYDMV2CFeE7VaIm9qDM54249443==>${HOSTINGER_API_TOKEN}
EOF

# 4. Run the rewrite:
git filter-repo --replace-text /tmp/replace.txt --force

# 5. Add the remote back (filter-repo strips it as a safety):
git remote add origin https://github.com/Kjdragan/universal_agent.git

# 6. Force-push every branch and tag:
git push origin --all --force-with-lease
git push origin --tags --force-with-lease
```

### 4.3 Post-rewrite cleanup

- **Notify all collaborators** to delete their local clone and re-clone.
- **On every machine** that has a checkout (the desktop, the VPS production
  checkout at `/opt/universal_agent`, any CI runner state):
  ```bash
  cd <repo>
  git fetch --all --prune
  git reset --hard origin/<branch>
  ```
- **Trigger a fresh deploy** so the production VPS picks up the rewritten
  history rather than continuing on the dangling old commits.
- **Delete and recreate any open PRs** — PR refs cache the pre-rewrite
  commits.
- **Delete any forks** that are no longer needed (each fork keeps the old
  commits).
- **Wait ~90 days** before assuming GitHub's dangling-commit cache is purged.
  In the meantime, the old commits remain accessible by SHA via direct URL.

### 4.4 Don't

- ❌ Don't run `git filter-branch` directly. It's deprecated and notoriously
  buggy on big repos. Use `git-filter-repo`.
- ❌ Don't force-push without `--force-with-lease`. Lease-checked force-push
  refuses if someone else pushed since your last fetch — important during a
  multi-branch rewrite.
- ❌ Don't expect GitHub secret-scanning to "un-flag" the leak. It will not.
  The audit trail of the alert remains regardless of what you do to the repo.

---

## 5. What's already done (you don't need to repeat)

- ✅ Migrated the token to Infisical (`production / HOSTINGER_API_TOKEN`).
  The user updated this 2026-05-08.
- ✅ Sanitized `.mcp.json:33` to `${HOSTINGER_API_TOKEN}` placeholder
  (commit `bcc5fd4c`).
- ✅ Documented the three MCP credentials in `.env.example`.
- ✅ Shipped `scripts/claude_with_mcp_env.sh` so interactive `claude`
  sessions get all Infisical secrets via the canonical `initialize_runtime_secrets()`
  path. MCP servers will start with the new (rotated) token automatically.

---

## 6. What the operator needs to do (checklist)

- [ ] **Revoke the old token in Hostinger UI** (token starting `ei5J…`).
      §3.
- [ ] **Verify revocation with curl** until you get `401/403`.
      §3 last code block.
- [ ] **Decide whether to rewrite git history.** Recommended: **no**, unless
      a compliance/audit reason requires it. Revocation alone is sufficient
      for this leak class.
- [ ] (If rewriting history) Follow §4. Plan for a multi-hour window with
      every collaborator notified.
- [ ] (Optional but useful) Add a pre-commit hook or CI gate that detects
      literal API-key patterns in `.mcp.json`. The current setup relies on
      GitGuardian (which already runs on PRs); a local hook would catch
      it before push. Track as a small follow-up if desired.

---

## 7. Cross-references

- **Sanitization commit:** `bcc5fd4c` — `fix(mcp): launcher uses initialize_runtime_secrets(); migrate Hostinger token to Infisical`.
- **Original commit:** `911ce4cc` — `Version shared memory snapshot and local MCP config files` (2026-02-19).
- **`.mcp.json` placeholder pattern:** see [`scripts/claude_with_mcp_env.sh`](../../scripts/claude_with_mcp_env.sh) and [`scripts/_claude_launcher.py`](../../scripts/_claude_launcher.py).
- **Canonical secrets pattern:** [`docs/deployment/secrets_and_environments.md`](../deployment/secrets_and_environments.md) and CLAUDE.md "never `.env` files or `os.getenv` for secrets" rule.
- **`git-filter-repo` reference:** https://github.com/newren/git-filter-repo
