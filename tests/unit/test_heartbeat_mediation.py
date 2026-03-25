from universal_agent.heartbeat_mediation import sanitize_heartbeat_recommendation_text


def test_sanitize_heartbeat_next_step_canonicalizes_acl_denial_replay_guidance() -> None:
    raw = (
        "Update Tailscale ACL to permit SSH from mint-desktop to uaonvps, "
        "OR add workstation IP to DigitalOcean firewall, "
        "OR manually run DLQ replay from VPS console."
    )

    assert sanitize_heartbeat_recommendation_text(raw, field="next_step") == (
        "Update Tailscale ACL/SSH policy to permit operator workstation access from "
        "mint-desktop to uaonvps, OR allowlist the workstation public IP in the "
        "VPS host firewall if you are using a public fallback path, OR manually run "
        "DLQ replay from the VPS console."
    )


def test_sanitize_heartbeat_summary_rewrites_stale_provider_language() -> None:
    raw = "SSH blocked by ACL policy. Add workstation IP to the DigitalOcean firewall or check DO firewall rules."

    assert sanitize_heartbeat_recommendation_text(raw, field="summary") == (
        "SSH blocked by ACL policy. allowlist the workstation public IP in the "
        "VPS host firewall if you are using a public fallback path or check VPS host firewall rules."
    )
