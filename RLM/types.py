from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CorpusDocument:
    path: Path
    rel_path: str
    char_count: int
    word_count: int


@dataclass
class CorpusBundle:
    source_path: Path
    documents: list[CorpusDocument]
    total_chars: int
    total_words: int
    estimated_tokens: int


@dataclass
class DistillRequest:
    mode: str
    topic: str
    report_title: str
    source: str | None = None
    workspace: str | None = None
    task_name: str | None = None
    output_dir: str = "RLM/work_products"
    threshold_tokens: int = 180_000
    enforce_threshold: bool = False
    model: str = "claude-sonnet-4-20250514"


@dataclass
class LaneResult:
    mode: str
    lane_dir: Path
    raw_artifacts: dict[str, str] = field(default_factory=dict)
    executive_summary: str = ""
    key_findings: list[str] = field(default_factory=list)
    evidence_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DistillResult:
    request: DistillRequest
    corpus: CorpusBundle
    run_dir: Path
    outputs: dict[str, str]
    lane_result: LaneResult
