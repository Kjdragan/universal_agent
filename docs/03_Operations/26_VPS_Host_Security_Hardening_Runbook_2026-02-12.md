# 26. VPS Host Security Hardening Runbook (Solo-Dev + Agentic Safe) (2026-02-12)

## Purpose
This runbook defines host-level hardening for the production VPS while preserving fast solo development and uninterrupted agentic execution.

Target environment:
1. VPS host: `root@100.106.113.93` (Tailscale)
2. App root: `/opt/universal_agent`
3. Core services:
   1. `universal-agent-gateway`
   2. `universal-agent-api`
   3. `universal-agent-webui`

## Design goals
1. Reduce external attack surface.
2. Keep direct operator velocity (single maintainer, rapid deploy loops).
3. Preserve outbound connectivity needed for agentic tool/API actions.
4. Avoid meaningful runtime performance degradation.

## Recommended hardening profile
1. SSH:
   1. `PermitRootLogin prohibit-password`
   2. `PasswordAuthentication no`
   3. `KbdInteractiveAuthentication no`
2. Firewall:
   1. Enable `ufw`.
   2. Allow inbound ports `22`, `80`, `443`.
   3. Keep outbound allow (default) for agentic external providers.
3. Brute-force controls:
   1. Install and enable `fail2ban` for `sshd`.
4. Secret hygiene:
   1. Keep `/opt/universal_agent/.env` on VPS.
   2. Set restrictive permissions (`chmod 600` by default).
5. Patch cycle:
   1. Keep unattended upgrades enabled.
   2. Reboot after kernel updates in a controlled window.

## Operational impact
1. Performance impact: negligible for normal Universal Agent runtime.
2. Development impact: minimal when using SSH keys.
3. Agentic behavior impact: none expected if outbound traffic remains allowed.

## Apply commands (staged)
Run from local machine.

### 1) Backup SSH config
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 \
"cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.$(date +%Y%m%d_%H%M%S)"
```

### 2) Apply SSH hardening (solo-safe)
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
set -e
sshd_cfg=/etc/ssh/sshd_config
sed -i "s/^#\?PermitRootLogin .*/PermitRootLogin prohibit-password/" "$sshd_cfg"
sed -i "s/^#\?PasswordAuthentication .*/PasswordAuthentication no/" "$sshd_cfg"
sed -i "s/^#\?KbdInteractiveAuthentication .*/KbdInteractiveAuthentication no/" "$sshd_cfg"
grep -q "^PermitRootLogin " "$sshd_cfg" || echo "PermitRootLogin prohibit-password" >> "$sshd_cfg"
grep -q "^PasswordAuthentication " "$sshd_cfg" || echo "PasswordAuthentication no" >> "$sshd_cfg"
grep -q "^KbdInteractiveAuthentication " "$sshd_cfg" || echo "KbdInteractiveAuthentication no" >> "$sshd_cfg"
sshd -t
systemctl reload ssh
'
```

### 3) Enable UFW with minimal inbound policy
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
set -e
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose
'
```

### 4) Install and enable fail2ban
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
set -e
apt update
apt install -y fail2ban
systemctl enable --now fail2ban
fail2ban-client status
'
```

### 5) Harden `.env` file permissions
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
set -e
chown root:root /opt/universal_agent/.env
chmod 600 /opt/universal_agent/.env
ls -l /opt/universal_agent/.env
'
```

## Validation commands
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
echo "=== SSHD ==="
sshd -T | egrep "permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication" | sort
echo "=== UFW ==="
ufw status verbose
echo "=== FAIL2BAN ==="
systemctl is-active fail2ban
fail2ban-client status
echo "=== UA SERVICES ==="
for s in universal-agent-gateway universal-agent-api universal-agent-webui; do
  printf "%s=" "$s"; systemctl is-active "$s"
done
'
```

## Rollback commands
If access behavior is not as expected, rollback SSH config from backup and reload:
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 '
set -e
latest_bak="$(ls -1t /etc/ssh/sshd_config.bak.* | head -n 1)"
cp "$latest_bak" /etc/ssh/sshd_config
sshd -t
systemctl reload ssh
'
```

To relax firewall quickly:
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 "ufw disable"
```

To stop fail2ban:
```bash
ssh -i ~/.ssh/id_ed25519 root@100.106.113.93 "systemctl disable --now fail2ban"
```

## Notes on agentic flexibility
1. Keep outbound network open unless you have a complete allowlist for all model/tool providers.
2. Host hardening should not replace app-level authorization and tool guardrails.
3. Keep SSH key hygiene strong (`~/.ssh` permissions, passphrase, backup key) because key auth becomes primary.
