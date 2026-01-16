"""
Universal Ralph Wrapper - Completion Evaluator

Evaluates whether tasks are complete using multiple strategies:
1. Binary checks (file exists, side effect recorded)
2. Constraint validation (min length, contains text)
3. LLM-as-judge qualitative evaluation
4. Composite evaluation combining all strategies
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .state import Task, Artifact, CompletionConfidence, ArtifactType


@dataclass
class EvaluationResult:
    """Result of evaluating task completion."""

    is_complete: bool
    confidence: CompletionConfidence
    overall_score: float

    binary_results: Dict[str, bool] = field(default_factory=dict)
    constraint_results: Dict[str, Tuple[bool, str]] = field(default_factory=dict)
    qualitative_score: Optional[float] = None
    qualitative_reasoning: Optional[str] = None

    missing_elements: List[str] = field(default_factory=list)
    suggested_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_complete": self.is_complete,
            "confidence": self.confidence.value,
            "overall_score": self.overall_score,
            "binary_results": self.binary_results,
            "constraint_results": {
                k: {"passed": v[0], "message": v[1]} for k, v in self.constraint_results.items()
            },
            "qualitative_score": self.qualitative_score,
            "qualitative_reasoning": self.qualitative_reasoning,
            "missing_elements": self.missing_elements,
            "suggested_actions": self.suggested_actions,
        }


class Evaluator(ABC):
    """Abstract base class for completion evaluators."""

    @abstractmethod
    def evaluate(
        self, task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> EvaluationResult:
        raise NotImplementedError


class BinaryCheckEvaluator(Evaluator):
    """Evaluates binary checks: file exists, side effect recorded, etc."""

    def __init__(self, state_manager=None):
        self.state_manager = state_manager

    def evaluate(
        self, task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> EvaluationResult:
        if not task.binary_checks:
            return EvaluationResult(
                is_complete=True,
                confidence=CompletionConfidence.DEFINITIVE,
                overall_score=1.0,
            )

        results: Dict[str, bool] = {}
        missing: List[str] = []

        for check in task.binary_checks:
            passed, message = self._evaluate_check(check, artifacts, agent_output, workspace_path)
            results[check] = passed
            if not passed:
                missing.append(message)

        all_passed = all(results.values())
        score = sum(results.values()) / len(results) if results else 1.0

        return EvaluationResult(
            is_complete=all_passed,
            confidence=CompletionConfidence.DEFINITIVE
            if all_passed
            else CompletionConfidence.FAILED,
            overall_score=score,
            binary_results=results,
            missing_elements=missing,
            suggested_actions=[f"Complete: {m}" for m in missing],
        )

    def _evaluate_check(
        self, check: str, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> Tuple[bool, str]:
        if check.startswith("file_exists:"):
            filename = check.split(":", 1)[1]
            artifacts_path = workspace_path / ".urw" / "artifacts" / filename
            if artifacts_path.exists():
                return True, ""
            if (workspace_path / filename).exists():
                return True, ""
            for art in artifacts:
                if art.file_path and art.file_path.endswith(filename):
                    return True, ""
            return False, f"File '{filename}' not found"

        if check.startswith("artifact_exists:"):
            artifact_id = check.split(":", 1)[1]
            for art in artifacts:
                if art.id == artifact_id:
                    return True, ""
            return False, f"Artifact '{artifact_id}' not produced"

        if check.startswith("side_effect:"):
            effect_type = check.split(":", 1)[1]
            if self.state_manager:
                effects = self.state_manager.conn.execute(
                    "SELECT id FROM side_effects WHERE effect_type = ?",
                    (effect_type,),
                ).fetchone()
                if effects:
                    return True, ""
            return False, f"Side effect '{effect_type}' not recorded"

        if check.startswith("contains:"):
            text = check.split(":", 1)[1]
            if text.lower() in agent_output.lower():
                return True, ""
            return False, f"Output doesn't contain '{text}'"

        return True, f"Unknown check type: {check}"


class ConstraintEvaluator(Evaluator):
    """Evaluates constraint checks: min/max length, contains patterns, etc."""

    def evaluate(
        self, task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> EvaluationResult:
        if not task.constraints:
            return EvaluationResult(
                is_complete=True,
                confidence=CompletionConfidence.DEFINITIVE,
                overall_score=1.0,
            )

        results: Dict[str, Tuple[bool, str]] = {}
        missing: List[str] = []

        content = self._get_primary_content(artifacts, workspace_path) or agent_output

        for constraint in task.constraints:
            constraint_key = f"{constraint['type']}:{constraint.get('value', '')}"
            passed, message = self._evaluate_constraint(constraint, content)
            results[constraint_key] = (passed, message)
            if not passed:
                missing.append(message)

        all_passed = all(r[0] for r in results.values())
        score = sum(1 for r in results.values() if r[0]) / len(results) if results else 1.0

        return EvaluationResult(
            is_complete=all_passed,
            confidence=CompletionConfidence.DEFINITIVE if all_passed else CompletionConfidence.LOW,
            overall_score=score,
            constraint_results=results,
            missing_elements=missing,
            suggested_actions=[f"Fix: {m}" for m in missing],
        )

    def _get_primary_content(
        self, artifacts: List[Artifact], workspace_path: Path
    ) -> Optional[str]:
        for artifact in artifacts:
            if artifact.artifact_type == ArtifactType.FILE and artifact.file_path:
                file_path = workspace_path / ".urw" / "artifacts" / artifact.file_path
                if file_path.exists():
                    try:
                        return file_path.read_text()
                    except Exception:
                        continue
        return None

    def _evaluate_constraint(self, constraint: Dict[str, Any], content: str) -> Tuple[bool, str]:
        ctype = constraint.get("type", "")
        value = constraint.get("value")

        if ctype == "min_length":
            if len(content) >= int(value):
                return True, ""
            return False, f"Content length {len(content)} < {value}"
        if ctype == "max_length":
            if len(content) <= int(value):
                return True, ""
            return False, f"Content length {len(content)} > {value}"
        if ctype == "contains":
            if value and str(value).lower() in content.lower():
                return True, ""
            return False, f"Content missing '{value}'"
        if ctype == "regex":
            if value and re.search(str(value), content, re.MULTILINE):
                return True, ""
            return False, f"Regex '{value}' not found"

        return True, f"Unknown constraint: {ctype}"


class LLMJudgeEvaluator(Evaluator):
    """Uses an LLM to evaluate qualitative completion."""

    def __init__(self, llm_client: Any, model: str = "claude-sonnet-4-20250514"):
        self.llm_client = llm_client
        self.model = model

    def evaluate(
        self, task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> EvaluationResult:
        rubric = task.evaluation_rubric or "Is the task complete and acceptable?"
        prompt = self._build_prompt(task, agent_output, rubric)

        response = self.llm_client.messages.create(
            model=self.model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = response.content[0].text
        score, reasoning = self._parse_response(response_text)

        confidence = self._score_to_confidence(score)
        is_complete = score >= task.minimum_acceptable_score

        return EvaluationResult(
            is_complete=is_complete,
            confidence=confidence,
            overall_score=score,
            qualitative_score=score,
            qualitative_reasoning=reasoning,
            suggested_actions=["Improve output to satisfy rubric"] if not is_complete else [],
        )

    def _build_prompt(self, task: Task, agent_output: str, rubric: str) -> str:
        return (
            "You are a strict evaluator. Rate the task output from 0 to 1.\n\n"
            f"Task: {task.title}\n"
            f"Description: {task.description}\n\n"
            f"Rubric: {rubric}\n\n"
            "Output (truncated):\n"
            f"{agent_output[:4000]}\n\n"
            "Return JSON: {\"score\": 0.0-1.0, \"reasoning\": \"...\"}"
        )

    def _parse_response(self, response_text: str) -> Tuple[float, str]:
        try:
            parsed = json.loads(response_text)
            score = float(parsed.get("score", 0.0))
            reasoning = str(parsed.get("reasoning", ""))
            return score, reasoning
        except Exception:
            return 0.0, "Failed to parse evaluator response"

    def _score_to_confidence(self, score: float) -> CompletionConfidence:
        if score >= 0.85:
            return CompletionConfidence.HIGH
        if score >= 0.6:
            return CompletionConfidence.MEDIUM
        if score >= 0.4:
            return CompletionConfidence.LOW
        return CompletionConfidence.UNCERTAIN


class CompositeEvaluator(Evaluator):
    """Combines binary, constraint, and LLM evaluation."""

    def __init__(self, llm_client: Any, state_manager=None):
        self.binary = BinaryCheckEvaluator(state_manager)
        self.constraints = ConstraintEvaluator()
        self.qualitative = LLMJudgeEvaluator(llm_client)

    def evaluate(
        self, task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> EvaluationResult:
        binary_res = self.binary.evaluate(task, artifacts, agent_output, workspace_path)
        if task.verification_type == "binary":
            return binary_res

        constraint_res = self.constraints.evaluate(task, artifacts, agent_output, workspace_path)
        if task.verification_type == "constraint":
            return constraint_res

        qualitative_res = None
        if task.verification_type in {"qualitative", "composite"}:
            qualitative_res = self.qualitative.evaluate(task, artifacts, agent_output, workspace_path)

        scores = [binary_res.overall_score, constraint_res.overall_score]
        if qualitative_res:
            scores.append(qualitative_res.overall_score)

        overall = sum(scores) / len(scores) if scores else 0.0
        is_complete = all(
            res.is_complete
            for res in [binary_res, constraint_res, qualitative_res]
            if res is not None
        )

        confidence = qualitative_res.confidence if qualitative_res else binary_res.confidence

        missing = binary_res.missing_elements + constraint_res.missing_elements
        suggested = binary_res.suggested_actions + constraint_res.suggested_actions

        return EvaluationResult(
            is_complete=is_complete,
            confidence=confidence,
            overall_score=overall,
            binary_results=binary_res.binary_results,
            constraint_results=constraint_res.constraint_results,
            qualitative_score=qualitative_res.qualitative_score if qualitative_res else None,
            qualitative_reasoning=qualitative_res.qualitative_reasoning if qualitative_res else None,
            missing_elements=missing,
            suggested_actions=suggested,
        )


def create_default_evaluator(llm_client: Any, state_manager=None) -> CompositeEvaluator:
    return CompositeEvaluator(llm_client, state_manager)


def quick_evaluate(task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path, llm_client: Any) -> EvaluationResult:
    evaluator = CompositeEvaluator(llm_client)
    return evaluator.evaluate(task, artifacts, agent_output, workspace_path)
