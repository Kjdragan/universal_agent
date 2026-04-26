import pytest
import tempfile
from pathlib import Path

from universal_agent.services.dag_loader import load_workflow, WorkflowValidationError


@pytest.mark.asyncio
async def test_dag_loader_yaml():
    """Test loading a valid workflow YAML file."""
    yaml_content = """\
nodes:
  - id: lint
    type: subprocess
    command: "ruff check ."
  - id: test
    type: subprocess
    command: "pytest tests/"
edges:
  - from: lint
    to: test
start: lint
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        path = Path(f.name)

    try:
        workflow = load_workflow(path)
        assert workflow["start"] == "lint"
        assert len(workflow["nodes"]) == 2
        assert len(workflow["edges"]) == 1
        assert workflow["nodes"][0]["id"] == "lint"
        assert workflow["nodes"][1]["command"] == "pytest tests/"
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_dag_loader_invalid_schema_missing_nodes():
    """Test that the loader rejects YAML missing required 'nodes' key."""
    yaml_content = """\
edges:
  - from: a
    to: b
start: a
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        path = Path(f.name)

    try:
        with pytest.raises(WorkflowValidationError, match="nodes"):
            load_workflow(path)
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_dag_loader_invalid_schema_missing_start():
    """Test that the loader rejects YAML missing required 'start' key."""
    yaml_content = """\
nodes:
  - id: a
    type: action
edges: []
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        path = Path(f.name)

    try:
        with pytest.raises(WorkflowValidationError, match="start"):
            load_workflow(path)
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_dag_loader_file_not_found():
    """Test that the loader raises FileNotFoundError for missing files."""
    with pytest.raises(FileNotFoundError):
        load_workflow(Path("/nonexistent/workflow.yaml"))
