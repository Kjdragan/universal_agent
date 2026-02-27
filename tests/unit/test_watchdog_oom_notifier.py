import importlib.util
from pathlib import Path


def _load_notifier_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "watchdog_oom_notifier.py"
    spec = importlib.util.spec_from_file_location("watchdog_oom_notifier", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_oom_lines_ignores_generic_sigkill(monkeypatch):
    module = _load_notifier_module()

    def fake_run(cmd):
        text = " ".join(cmd)
        if "-k" in text:
            return ""
        return "universal-agent-gateway.service: Main process exited, code=killed, status=9/KILL\n"

    monkeypatch.setattr(module, "_run", fake_run)
    lines = module._collect_oom_lines(0)
    assert lines == []


def test_collect_oom_lines_detects_service_oom_killer_signal(monkeypatch):
    module = _load_notifier_module()

    def fake_run(cmd):
        text = " ".join(cmd)
        if "-k" in text:
            return ""
        return "universal-agent-gateway.service: A process of this unit has been killed by the OOM killer.\n"

    monkeypatch.setattr(module, "_run", fake_run)
    lines = module._collect_oom_lines(0)
    assert len(lines) == 1
    assert "OOM killer" in lines[0]


def test_collect_oom_lines_detects_kernel_oom_entries(monkeypatch):
    module = _load_notifier_module()

    def fake_run(cmd):
        text = " ".join(cmd)
        if "-k" in text:
            return "kernel: Out of memory: Killed process 1234 (python3)\n"
        return ""

    monkeypatch.setattr(module, "_run", fake_run)
    lines = module._collect_oom_lines(0)
    assert len(lines) == 1
    assert "Out of memory" in lines[0]
