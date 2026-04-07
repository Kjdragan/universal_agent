from __future__ import annotations

from pathlib import Path


def test_llm_wiki_docs_are_indexed():
    readme = Path("docs/README.md").read_text(encoding="utf-8")
    status = Path("docs/Documentation_Status.md").read_text(encoding="utf-8")

    assert "02_Subsystems/LLM_Wiki_System.md" in readme
    assert "109_LLM_Wiki_Implementation_Status_2026-04-06.md" in readme
    assert "LLM_Wiki_System.md" in status
    assert "109 | LLM Wiki Implementation Status" in status
