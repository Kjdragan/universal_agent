from __future__ import annotations

from pathlib import Path

from csi_ingester.config import load_config


def test_instance_id_prefers_env_override(monkeypatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("csi:\n  instance_id: csi-local-01\n", encoding="utf-8")
    monkeypatch.setenv("CSI_INSTANCE_ID", "csi-vps-01")
    config = load_config(str(cfg))
    assert config.instance_id == "csi-vps-01"
