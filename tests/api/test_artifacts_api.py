import importlib
import os
from pathlib import Path

from fastapi.testclient import TestClient


def _load_app_with_artifacts_dir(tmp_path: Path):
    os.environ["UA_ARTIFACTS_DIR"] = str(tmp_path)
    # Import after setting env so module-level ARTIFACTS_DIR uses tmp_path.
    import universal_agent.api.server as server
    importlib.reload(server)
    return server.app


def test_artifacts_list_empty(tmp_path: Path):
    app = _load_app_with_artifacts_dir(tmp_path)
    client = TestClient(app)

    resp = client.get("/api/artifacts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["files"] == []


def test_artifacts_file_roundtrip(tmp_path: Path):
    app = _load_app_with_artifacts_dir(tmp_path)
    client = TestClient(app)

    (tmp_path / "hello.txt").write_text("hi", encoding="utf-8")
    resp = client.get("/api/artifacts/files/hello.txt")
    assert resp.status_code == 200
    assert resp.text == "hi"


def test_artifacts_path_traversal_blocked(tmp_path: Path):
    app = _load_app_with_artifacts_dir(tmp_path)
    client = TestClient(app)

    resp = client.get("/api/artifacts", params={"path": ".."})
    assert resp.status_code == 403

    # URL-encode traversal so the router doesn't normalize it away before our handler.
    resp = client.get("/api/artifacts/files/%2e%2e%2Fnope.txt")
    assert resp.status_code == 403
