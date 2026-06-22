"""Pure-logic tests for the auth-mode optimizer building blocks (no live models)."""
import os

from universal_agent.services.inference_auth import build_inference_env
from universal_agent.services.skill_triggering_eval import (
    _rewrite_description,
    _skill_name_from_md,
)


def test_anthropic_mode_scrubs_anthropic_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-scrubbed")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.z.ai/api/anthropic")
    env, model, resolved = build_inference_env("anthropic")
    assert resolved == "anthropic"
    assert model == "claude-opus-4-8"
    assert not any(k.startswith("ANTHROPIC_") for k in env), "anthropic mode must scrub ANTHROPIC_*"
    assert "CLAUDECODE" not in env


def test_rewrite_description_single_line():
    md = "---\nname: foo\ndescription: old one-liner\n---\n# Foo\nbody\n"
    out = _rewrite_description(md, "new\ndescription\ntext")
    assert "description: new description text" in out  # collapsed to one line
    assert "old one-liner" not in out
    assert "name: foo" in out  # other frontmatter preserved


def test_rewrite_description_none_is_noop():
    md = "---\nname: foo\ndescription: keep me\n---\nbody\n"
    assert _rewrite_description(md, None) == md


def test_skill_name_parse():
    assert _skill_name_from_md("---\nname: my-skill\n---\n", "fallback") == "my-skill"
    assert _skill_name_from_md("---\n---\n", "fallback") == "fallback"
