from __future__ import annotations

import re
from typing import Any

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
    "mint-desktop to uaonvps, OR allowlist the workstation public IP in the "
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


def derive_heartbeat_operator_review(
    payload: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    """Deterministic backstop for heartbeat investigation triage.

    A heartbeat finding set is frequently heterogeneous: a false alarm and a
    real outage can co-occur. The investigation LLM is asked to triage each
    finding independently and emit a ``findings_triage`` list, but we must not
    trust a single global ``operator_review_required`` flag to gate operator
    visibility — a false positive in one finding once masked a genuine
    multi-adapter CSI outage by getting the whole set labelled false_positive.

    This function ORs the LLM's global flag with a per-finding scan so that any
    finding the investigation marked ``real`` (or could not positively clear as
    a false positive while being ``critical``) forces operator review,
    regardless of the headline classification.

    Returns ``(operator_review_required, triage_entries)``.
    """
    explicit = bool(payload.get("operator_review_required"))
    triage_raw = payload.get("findings_triage")
    triage: list[dict[str, Any]] = (
        [entry for entry in triage_raw if isinstance(entry, dict)]
        if isinstance(triage_raw, list)
        else []
    )

    review = explicit
    for entry in triage:
        verdict = str(entry.get("verdict") or "").strip().lower()
        severity = str(entry.get("severity") or "").strip().lower()
        if bool(entry.get("operator_review_required")):
            review = True
        if verdict in {"real", "true_positive"}:
            review = True
        # A critical finding that was NOT positively cleared as a false positive
        # must always reach the operator — never let it be dismissed silently.
        if severity in {"critical", "error"} and verdict != "false_positive":
            review = True
    return review, triage

