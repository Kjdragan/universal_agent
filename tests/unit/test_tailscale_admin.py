import json

import pytest

from universal_agent.tailscale_admin import (
    TailscaleAdminError,
    TailscaleDevice,
    find_device_by_hostname,
    load_device_roles,
    merge_policy_overlay,
)


def test_merge_policy_overlay_appends_unique_ssh_rules_and_preserves_existing_values() -> None:
    base = {
        "ssh": [
            {"action": "accept", "src": ["tag:ci-gha"], "dst": ["tag:vps"], "users": ["root", "ua"]},
        ],
        "tagOwners": {"tag:vps": ["autogroup:admin"]},
    }
    overlay = {
        "ssh": [
            {"action": "accept", "src": ["tag:operator-workstation"], "dst": ["tag:vps"], "users": ["root", "ua"]},
            {"action": "accept", "src": ["tag:ci-gha"], "dst": ["tag:vps"], "users": ["root", "ua"]},
        ],
    }

    merged = merge_policy_overlay(base, overlay)

    assert merged["tagOwners"] == {"tag:vps": ["autogroup:admin"]}
    assert merged["ssh"] == [
        {"action": "accept", "src": ["tag:ci-gha"], "dst": ["tag:vps"], "users": ["root", "ua"]},
        {"action": "accept", "src": ["tag:operator-workstation"], "dst": ["tag:vps"], "users": ["root", "ua"]},
    ]


def test_load_device_roles_normalizes_tags(tmp_path) -> None:
    roles_path = tmp_path / "device_roles.json"
    roles_path.write_text(
        json.dumps(
            {
                "devices": {
                    "mint-desktop": ["tag:operator-workstation", "tag:operator-workstation"],
                    "srv1360701": "tag:vps",
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_device_roles(roles_path) == {
        "mint-desktop": ["tag:operator-workstation"],
        "srv1360701": ["tag:vps"],
    }


def test_load_device_roles_rejects_invalid_shape(tmp_path) -> None:
    roles_path = tmp_path / "device_roles.json"
    roles_path.write_text(json.dumps({"devices": {"mint-desktop": [None]}}), encoding="utf-8")

    with pytest.raises(TailscaleAdminError):
        load_device_roles(roles_path)


def test_find_device_by_hostname_matches_magicdns_name() -> None:
    device = TailscaleDevice(
        device_id="123",
        node_id="node-1",
        name="srv1360701.taildcc090.ts.net",
        hostname="srv1360701",
        tags=["tag:vps"],
        addresses=["100.106.113.93"],
        raw={},
    )

    assert find_device_by_hostname([device], "srv1360701") == device
    assert find_device_by_hostname([device], "srv1360701.taildcc090.ts.net") == device
