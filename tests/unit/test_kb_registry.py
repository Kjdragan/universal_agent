import json
import pytest
from pathlib import Path
from unittest.mock import patch

from universal_agent.wiki.kb_registry import (
    register_kb,
    get_kb,
    list_kbs,
    update_kb,
    remove_kb,
    get_registry_path,
)

@pytest.fixture
def mock_registry_dir(tmp_path):
    with patch("universal_agent.wiki.kb_registry.resolve_artifacts_dir", return_value=str(tmp_path)):
        yield tmp_path

def test_get_registry_path(mock_registry_dir):
    path = get_registry_path()
    assert str(mock_registry_dir) in str(path)
    assert path.name == "kb_registry.json"
    assert path.parent.name == "knowledge-bases"

def test_register_and_get_kb(mock_registry_dir):
    kb = register_kb("test-kb", "nb_123", "Test KB", tags=["test"])
    assert kb["notebook_id"] == "nb_123"
    assert kb["title"] == "Test KB"
    assert kb["tags"] == ["test"]
    assert kb["source_count"] == 0
    assert kb["last_queried"] is None
    
    fetched = get_kb("test-kb")
    assert fetched is not None
    assert fetched["notebook_id"] == "nb_123"

def test_list_empty_registry(mock_registry_dir):
    kbs = list_kbs()
    assert len(kbs) == 0

def test_list_populated_registry(mock_registry_dir):
    register_kb("kb-1", "id1", "Title 1")
    register_kb("kb-2", "id2", "Title 2")
    
    kbs = list_kbs()
    assert len(kbs) == 2
    slugs = {kb["slug"] for kb in kbs}
    assert slugs == {"kb-1", "kb-2"}

def test_update_kb_metadata(mock_registry_dir):
    register_kb("update-kb", "id-update", "Update KB")
    updated = update_kb("update-kb", source_count=5, last_queried="2026-04-08T00:00:00Z")
    
    assert updated["source_count"] == 5
    assert updated["last_queried"] == "2026-04-08T00:00:00Z"
    
    fetched = get_kb("update-kb")
    assert fetched["source_count"] == 5

def test_update_nonexistent_kb(mock_registry_dir):
    with pytest.raises(ValueError):
        update_kb("nope", source_count=1)

def test_remove_kb(mock_registry_dir):
    register_kb("remove-kb", "id-rem", "Rem KB")
    assert get_kb("remove-kb") is not None
    
    assert remove_kb("remove-kb") is True
    assert get_kb("remove-kb") is None
    assert remove_kb("remove-kb") is False

def test_registry_file_persistence(mock_registry_dir):
    register_kb("persist-kb", "id-persist", "Persist KB")
    
    path = get_registry_path()
    assert path.exists()
    
    data = json.loads(path.read_text())
    assert "persist-kb" in data
