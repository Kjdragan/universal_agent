"""YAML workflow loader and validator for the DAG Runner.

Loads workflow definitions from YAML files, validates the required schema
(nodes, edges, start), and returns a normalized Python dict.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


class WorkflowValidationError(ValueError):
    """Raised when a workflow definition fails schema validation."""


_REQUIRED_KEYS = ("nodes", "edges", "start")


def load_workflow(path: Path) -> Dict[str, Any]:
    """Load and validate a workflow definition from a YAML file.

    Args:
        path: Path to a ``.yaml`` or ``.yml`` file.

    Returns:
        A validated workflow dict with ``nodes``, ``edges``, and ``start``.

    Raises:
        FileNotFoundError: If *path* does not exist.
        WorkflowValidationError: If the file is malformed or missing required keys.
    """
    import yaml  # lazy import — only needed when loading YAML files

    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Workflow file not found: {resolved}")

    logger.info("Loading DAG workflow from %s", resolved)

    with open(resolved) as fh:
        try:
            data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise WorkflowValidationError(
                f"Invalid YAML in workflow file {resolved}: {exc}"
            ) from exc

    if not isinstance(data, dict):
        raise WorkflowValidationError(
            f"Workflow file must contain a YAML mapping, got {type(data).__name__}"
        )

    _validate_schema(data, source=str(resolved))
    return data


def validate_workflow_dict(workflow: Dict[str, Any]) -> None:
    """Validate an in-memory workflow definition dict.

    Raises:
        WorkflowValidationError: If the dict is missing required keys.
    """
    _validate_schema(workflow, source="inline")


def _validate_schema(data: Dict[str, Any], *, source: str) -> None:
    """Check that all required top-level keys are present."""
    for key in _REQUIRED_KEYS:
        if key not in data:
            raise WorkflowValidationError(
                f"Workflow definition ({source}) is missing required key '{key}'. "
                f"Required keys: {', '.join(_REQUIRED_KEYS)}"
            )

    nodes = data["nodes"]
    if not isinstance(nodes, list) or not nodes:
        raise WorkflowValidationError(
            f"Workflow definition ({source}) 'nodes' must be a non-empty list."
        )

    for idx, node in enumerate(nodes):
        if not isinstance(node, dict) or "id" not in node:
            raise WorkflowValidationError(
                f"Workflow definition ({source}) node at index {idx} must be a "
                "mapping with at least an 'id' key."
            )
