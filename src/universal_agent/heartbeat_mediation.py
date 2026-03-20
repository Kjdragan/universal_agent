from __future__ import annotations

import re

# Normalize any cloud provider mention to the canonical provider (Hostinger).
# Pink elephant approach: state what it IS, never mention incorrect providers.
_CLOUD_PROVIDER_RE = re.compile(
    r"\b(?:DigitalOcean|Linode|Vultr|AWS|GCP|Azure|Hetzner|Contabo|OVH)\b",
    re.IGNORECASE,
)
_PROVIDER_FIREWALL_RE = re.compile(
    r"add workstation IP to (?:the )?(?:DigitalOcean|DO|cloud provider|VPS provider) firewall",
    re.IGNORECASE,
)
_DO_FIREWALL_RE = re.compile(r"\bDO firewall\b", re.IGNORECASE)

_CANONICAL_SSH_BLOCKED_NEXT_STEP = (
    "Update Tailscale ACL/SSH policy to permit operator workstation access from "
    "mint-desktop to srv1360701, OR allowlist the workstation public IP in the "
    "VPS host firewall if you are using a public fallback path, OR manually run "
    "DLQ replay from the VPS console."
)


def sanitize_heartbeat_recommendation_text(text: str, *, field: str = "generic") -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""

    lowered = cleaned.lower()
    if (
        field == "next_step"
        and ("tailscale acl" in lowered or "tailscale ssh" in lowered or "ssh blocked by acl" in lowered)
        and ("dlq replay" in lowered or "vps console" in lowered)
    ):
        return _CANONICAL_SSH_BLOCKED_NEXT_STEP

    cleaned = _PROVIDER_FIREWALL_RE.sub(
        "allowlist the workstation public IP in the VPS host firewall if you are using a public fallback path",
        cleaned,
    )
    cleaned = _DO_FIREWALL_RE.sub("VPS host firewall", cleaned)
    cleaned = _CLOUD_PROVIDER_RE.sub("Hostinger VPS", cleaned)
    return cleaned

