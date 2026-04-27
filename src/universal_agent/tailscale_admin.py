from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import httpx


class TailscaleAdminError(RuntimeError):
    pass


def _tailnet_segment(tailnet: str) -> str:
    return httpx.URL(f"https://api.tailscale.com/{tailnet}").path.strip("/")


def _normalize_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _merge_list(base: list[Any], overlay: list[Any]) -> list[Any]:
    merged = list(base)
    seen = {_normalize_json(item) for item in merged}
    for item in overlay:
        token = _normalize_json(item)
        if token in seen:
            continue
        merged.append(item)
        seen.add(token)
    return merged


def merge_policy_overlay(base: Any, overlay: Any) -> Any:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, overlay_value in overlay.items():
            if key in merged:
                merged[key] = merge_policy_overlay(merged[key], overlay_value)
            else:
                merged[key] = overlay_value
        return merged
    if isinstance(base, list) and isinstance(overlay, list):
        return _merge_list(base, overlay)
    return overlay


def load_policy_overlay(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TailscaleAdminError(f"Policy overlay must be a JSON object: {path}")
    return data


def load_device_roles(path: Path) -> dict[str, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    devices = data.get("devices") if isinstance(data, dict) else None
    if not isinstance(devices, dict):
        raise TailscaleAdminError(f"Device roles file must contain a top-level 'devices' object: {path}")
    normalized: dict[str, list[str]] = {}
    for host, tags in devices.items():
        if not isinstance(host, str) or not host.strip():
            raise TailscaleAdminError(f"Invalid device hostname in roles file: {host!r}")
        if isinstance(tags, str):
            normalized[host.strip()] = [tags.strip()]
            continue
        if not isinstance(tags, list) or not all(isinstance(tag, str) and tag.strip() for tag in tags):
            raise TailscaleAdminError(f"Device roles for {host!r} must be a string or list of non-empty strings")
        normalized[host.strip()] = sorted({tag.strip() for tag in tags})
    return normalized


@dataclass
class TailscaleACLState:
    policy: dict[str, Any]
    etag: str


@dataclass
class TailscaleDevice:
    device_id: str
    node_id: str
    name: str
    hostname: str
    tags: list[str]
    addresses: list[str]
    raw: dict[str, Any]


class TailscaleAdminClient:
    def __init__(
        self,
        *,
        tailnet: str,
        api_token: str,
        base_url: str = "https://api.tailscale.com",
        timeout_seconds: float = 30.0,
    ) -> None:
        token = str(api_token or "").strip()
        if not token:
            raise TailscaleAdminError("Missing Tailscale admin API token")
        self.tailnet = str(tailnet or "").strip()
        if not self.tailnet:
            raise TailscaleAdminError("Missing Tailscale tailnet name")
        self._tailnet_segment = _tailnet_segment(self.tailnet)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            auth=(token, ""),
            headers={"User-Agent": "universal-agent/tailscale-admin"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TailscaleAdminClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, **kwargs)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                payload = response.json()
                detail = str(payload.get("message") or payload.get("error") or "").strip()
            except Exception:
                detail = response.text.strip()
            if detail:
                raise TailscaleAdminError(f"Tailscale API {method} {path} failed: {detail}") from exc
            raise TailscaleAdminError(
                f"Tailscale API {method} {path} failed with status {response.status_code}"
            ) from exc
        return response

    def get_acl(self) -> TailscaleACLState:
        response = self._request("GET", f"/api/v2/tailnet/{self._tailnet_segment}/acl")
        payload = response.json()
        if not isinstance(payload, dict):
            raise TailscaleAdminError("Unexpected ACL response shape from Tailscale API")
        etag = response.headers.get("etag", "").strip().strip('"')
        return TailscaleACLState(policy=payload, etag=etag)

    def validate_acl(self, policy: dict[str, Any]) -> None:
        self._request("POST", f"/api/v2/tailnet/{self._tailnet_segment}/acl/validate", json=policy)

    def apply_acl(self, policy: dict[str, Any], *, etag: str = "") -> None:
        headers: dict[str, str] = {}
        if etag:
            headers["If-Match"] = f'"{etag}"'
        self._request("POST", f"/api/v2/tailnet/{self._tailnet_segment}/acl", json=policy, headers=headers)

    def list_devices(self) -> list[TailscaleDevice]:
        response = self._request(
            "GET",
            f"/api/v2/tailnet/{self._tailnet_segment}/devices",
            params={"fields": "all"},
        )
        payload = response.json()
        items = payload.get("devices") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise TailscaleAdminError("Unexpected devices response shape from Tailscale API")
        devices: list[TailscaleDevice] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            devices.append(
                TailscaleDevice(
                    device_id=str(item.get("id") or "").strip(),
                    node_id=str(item.get("nodeId") or "").strip(),
                    name=str(item.get("name") or "").strip(),
                    hostname=str(item.get("hostname") or "").strip(),
                    tags=sorted({str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()}),
                    addresses=[str(addr).strip() for addr in (item.get("addresses") or []) if str(addr).strip()],
                    raw=item,
                )
            )
        return devices

    def set_device_tags(self, device_id: str, tags: list[str]) -> None:
        normalized = sorted({str(tag).strip() for tag in tags if str(tag).strip()})
        self._request("POST", f"/api/v2/device/{device_id}/tags", json={"tags": normalized})


def find_device_by_hostname(devices: list[TailscaleDevice], hostname: str) -> TailscaleDevice | None:
    wanted = str(hostname or "").strip().lower().rstrip(".")
    if not wanted:
        return None
    for device in devices:
        candidates = {
            device.hostname.lower().rstrip("."),
            device.name.lower().rstrip("."),
        }
        if device.name.lower().endswith(".ts.net"):
            candidates.add(device.name.lower().split(".", 1)[0])
        if device.hostname.lower().endswith(".ts.net"):
            candidates.add(device.hostname.lower().split(".", 1)[0])
        if wanted in candidates:
            return device
    return None
