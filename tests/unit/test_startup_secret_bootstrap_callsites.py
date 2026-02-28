import ast
from pathlib import Path


TARGET_FILES = [
    "src/universal_agent/main.py",
    "src/universal_agent/agent_core.py",
    "src/universal_agent/agent_setup.py",
    "src/universal_agent/gateway_server.py",
]

DISALLOWED_TOP_LEVEL_CALLS = {"initialize_runtime_secrets", "apply_xai_key_aliases"}


def _module_level_calls(source: str) -> set[str]:
    tree = ast.parse(source)
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name):
                found.add(func.id)
            elif isinstance(func, ast.Attribute):
                found.add(func.attr)
    return found


def test_no_module_level_secret_bootstrap_calls():
    repo_root = Path(__file__).resolve().parents[2]
    for rel_path in TARGET_FILES:
        path = repo_root / rel_path
        calls = _module_level_calls(path.read_text(encoding="utf-8"))
        offending = sorted(calls & DISALLOWED_TOP_LEVEL_CALLS)
        assert not offending, f"{rel_path} has disallowed module-level calls: {offending}"
