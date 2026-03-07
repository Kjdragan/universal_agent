from __future__ import annotations

from pathlib import Path

from csi_ingester.config import load_config


def test_instance_id_prefers_env_override(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("csi:\n  instance_id: csi-local-01\n", encoding="utf-8")
    monkeypatch.setenv("CSI_INSTANCE_ID", "csi-vps-01")
    config = load_config(str(cfg))
    assert config.instance_id == "csi-vps-01"


def test_threads_credential_properties_read_from_env(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("csi:\n  instance_id: csi-local-01\n", encoding="utf-8")
    monkeypatch.setenv("THREADS_APP_ID", "app-id")
    monkeypatch.setenv("THREADS_APP_SECRET", "app-secret")
    monkeypatch.setenv("THREADS_USER_ID", "12345")
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "token")
    monkeypatch.setenv("THREADS_TOKEN_EXPIRES_AT", "2026-12-01T00:00:00Z")
    config = load_config(str(cfg))
    assert config.threads_app_id == "app-id"
    assert config.threads_app_secret == "app-secret"
    assert config.threads_user_id == "12345"
    assert config.threads_access_token == "token"
    assert config.threads_token_expires_at == "2026-12-01T00:00:00Z"
