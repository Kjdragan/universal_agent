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
from .evaluation_policy import resolve_evaluation_policy, get_policy_summary


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
            if (workspace_path / "workspace_artifacts" / filename).exists():
                return True, ""
            if (workspace_path / "work_products" / filename).exists():
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
                # Check .urw/artifacts first (legacy/state-managed)
                paths_to_check = [
                    workspace_path / ".urw" / "artifacts" / artifact.file_path,
                    workspace_path / artifact.file_path
                ]
                
                for file_path in paths_to_check:
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

    async def evaluate_async(
        self, task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> EvaluationResult:
        rubric = task.evaluation_rubric or "Is the task complete and acceptable?"
        prompt = self._build_prompt(task, agent_output, rubric)

        # Helper to get raw Anthropic client from wrapper
        anthropic = self.llm_client
        if hasattr(self.llm_client, "client"):
            anthropic = self.llm_client.client
        elif hasattr(self.llm_client, "_client"):
            anthropic = self.llm_client._client
            
        try:
            # Check if we have a raw client (AsyncAnthropic) or need to use wrapper methods
            if hasattr(anthropic, "messages") and hasattr(anthropic.messages, "create"):
                response = await anthropic.messages.create(
                    model=self.model,
                    max_tokens=4000,
                    messages=[{"role": "user", "content": prompt}]
                )
            else:
                # If we still can't find .messages.create, maybe it's the SDK client itself
                # usage: client.make_request(...) or similar. 
                # For now, let's assume if it has no messages.create, we can't use it easily in this raw way.
                # BUT, let's try to see if it's the ClaudeSDKClient which might have a different interface.
                # Actually, the error was "AttributeError: 'ClaudeSDKClient' object has no attribute 'messages'"
                # So we failed to unwrap it.
                # Let's try to access the underlying client via the 'tool_executor' or similar if it exists?
                # Or just construct a new AsyncAnthropic client from env vars if all else fails?
                
                # FALLBACK: Create a fresh client from env vars (Safest fallback)
                import os
                from anthropic import AsyncAnthropic
                api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ZAI_API_KEY")
                if not api_key:
                    raise ValueError("Evaluator cannot find API key to create fallback client.")
                
                fallback_client = AsyncAnthropic(api_key=api_key, base_url=os.getenv("ANTHROPIC_BASE_URL"))
                response = await fallback_client.messages.create(
                    model=self.model,
                    max_tokens=4000,
                    messages=[{"role": "user", "content": prompt}]
                )

        except Exception as e:
             raise ValueError(f"Evaluator LLM failure: {e}")
        response_text = response.content[0].text
        score, reasoning = self._parse_response(response_text)

        confidence = self._score_to_confidence(score)
        is_complete = score >= task.minimum_acceptable_score

        missing_elements = []
        if not is_complete and reasoning:
            missing_elements.append(f"Judge Reasoning: {reasoning}")

        return EvaluationResult(
            is_complete=is_complete,
            confidence=confidence,
            overall_score=score,
            qualitative_score=score,
            qualitative_reasoning=reasoning,
            missing_elements=missing_elements,
            suggested_actions=["Improve output to satisfy rubric"] if not is_complete else [],
        )

    def evaluate(
        self, task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> EvaluationResult:
        """Sync wrapper - runs async evaluation in event loop."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            # If there's already a running loop, create a task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.evaluate_async(task, artifacts, agent_output, workspace_path)
                )
                return future.result()
        except RuntimeError:
            # No running loop, we can just run it
            return asyncio.run(
                self.evaluate_async(task, artifacts, agent_output, workspace_path)
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
        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1]).strip()

        def _coerce_score(val: object) -> float:
            try:
                score_val = float(val)
            except (TypeError, ValueError):
                return 0.0
            return max(0.0, min(1.0, score_val))

        try:
            parsed = json.loads(cleaned)
            score = _coerce_score(parsed.get("score", 0.0))
            reasoning = str(parsed.get("reasoning", ""))
            return score, reasoning
        except Exception:
            pass

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                score = _coerce_score(parsed.get("score", 0.0))
                reasoning = str(parsed.get("reasoning", ""))
                return score, reasoning
            except Exception:
                pass

        match = re.search(r"score\s*[:=]\s*([01](?:\.\d+)?)", cleaned, re.IGNORECASE)
        if match:
            score = _coerce_score(match.group(1))
            return score, cleaned

        return 0.0, "Failed to parse evaluator response"

    def _score_to_confidence(self, score: float) -> CompletionConfidence:
        if score >= 0.85:
            return CompletionConfidence.HIGH
        if score >= 0.6:
            return CompletionConfidence.MEDIUM
        if score >= 0.4:
            return CompletionConfidence.LOW
        return CompletionConfidence.UNCERTAIN


class SubAgentEvaluator(Evaluator):
    """Uses evaluation-judge sub-agent for qualitative assessment.
    
    This evaluator delegates evaluation to the evaluation-judge sub-agent
    which has tool access (Read, Grep, list_directory) to inspect artifacts.
    """

    def __init__(self, agent_adapter: Any, workspace_path: Path):
        """
        Args:
            agent_adapter: UniversalAgentAdapter with invoke_subagent() method
            workspace_path: Workspace path for file operations
        """
        self.agent_adapter = agent_adapter
        self.workspace_path = workspace_path

    async def evaluate_async(
        self, task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> EvaluationResult:
        """Async evaluation using sub-agent."""
        prompt = self._build_evaluation_prompt(task, artifacts, agent_output)
        
        result = await self.agent_adapter.invoke_subagent(
            agent_type="evaluation-judge",
            prompt=prompt,
            workspace_path=workspace_path,
        )
        
        return self._parse_verdict(result, task)

    def evaluate(
        self, task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> EvaluationResult:
        """Sync wrapper - runs async evaluation in event loop."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            # If there's already a running loop, create a task
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.evaluate_async(task, artifacts, agent_output, workspace_path)
                )
                return future.result()
        except RuntimeError:
            # No running loop, we can just run it
            return asyncio.run(
                self.evaluate_async(task, artifacts, agent_output, workspace_path)
            )

    def _build_evaluation_prompt(
        self, task: Task, artifacts: List[Artifact], agent_output: str
    ) -> str:
        """Build prompt for evaluation-judge sub-agent."""
        artifact_paths = [a.file_path for a in artifacts if a.file_path]
        
        criteria = []
        if task.binary_checks:
            criteria.extend([f"Binary: {c}" for c in task.binary_checks])
        if task.constraints:
            criteria.extend([f"Constraint: {c['type']}={c.get('value', '')}" for c in task.constraints])
        if task.evaluation_rubric:
            criteria.append(f"Qualitative: {task.evaluation_rubric}")
        
        return f"""# Task Evaluation Request

## Task Details
- **Title:** {task.title}
- **Description:** {task.description}

## Success Criteria
{chr(10).join(f"- {c}" for c in criteria) if criteria else "- Task should be completed as described"}

## Expected Artifacts
{chr(10).join(f"- {p}" for p in artifact_paths) if artifact_paths else "- Check workspace for relevant outputs"}

## Workspace Path
{self.workspace_path}

## Agent Output Summary
{agent_output[:2000] if agent_output else "(No text output)"}

---

**Instructions:**
1. Use `list_directory` to check what files exist
2. Use `Read` to inspect file contents where needed
3. Evaluate against the success criteria above
4. Return your verdict as structured JSON
"""

    def _parse_verdict(self, result: Dict[str, Any], task: Task) -> EvaluationResult:
        """Parse sub-agent verdict into EvaluationResult."""
        verdict = result.get("verdict") or {}
        
        is_complete = verdict.get("is_complete", False)
        confidence_val = verdict.get("confidence", 0.5)
        reasoning = verdict.get("reasoning", "")
        
        # Map confidence float to enum
        if confidence_val >= 0.85:
            confidence = CompletionConfidence.HIGH
        elif confidence_val >= 0.6:
            confidence = CompletionConfidence.MEDIUM
        elif confidence_val >= 0.4:
            confidence = CompletionConfidence.LOW
        else:
            confidence = CompletionConfidence.UNCERTAIN
        
        # Extract check results if available
        checks = verdict.get("checks_performed", [])
        binary_results = {}
        for check in checks:
            check_name = check.get("check", "")
            passed = check.get("passed", False)
            binary_results[check_name] = passed
        
        return EvaluationResult(
            is_complete=is_complete,
            confidence=confidence,
            overall_score=confidence_val,
            binary_results=binary_results,
            qualitative_score=confidence_val,
            qualitative_reasoning=reasoning,
            missing_elements=verdict.get("missing_elements", []),
            suggested_actions=verdict.get("suggested_actions", []),
        )


class CompositeEvaluator(Evaluator):
    """Combines binary, constraint, and LLM evaluation with policy overrides."""

    def __init__(
        self,
        llm_client: Any,
        state_manager=None,
        model: str = "claude-sonnet-4-20250514",
        evaluation_policy: Optional[Dict[str, Any]] = None,
    ):
        self.binary = BinaryCheckEvaluator(state_manager)
        self.constraints = ConstraintEvaluator()
        self.qualitative = LLMJudgeEvaluator(llm_client, model=model)
        self.evaluation_policy = evaluation_policy or {}

    def _resolve_policy(self, task: Task) -> Dict[str, Any]:
        """Resolve evaluation policy using centralized resolver."""
        return resolve_evaluation_policy(
            task=task,
            global_policy=self.evaluation_policy,
            template_name=None,  # Could be passed from orchestrator in future
        )

    def evaluate(
        self, task: Task, artifacts: List[Artifact], agent_output: str, workspace_path: Path
    ) -> EvaluationResult:
        policy = self._resolve_policy(task)

        require_binary = policy.get("require_binary")
        if require_binary is None:
            require_binary = bool(task.binary_checks)

        require_constraints = policy.get("require_constraints")
        if require_constraints is None:
            require_constraints = bool(task.constraints)

        require_qualitative = policy.get("require_qualitative")
        if require_qualitative is None:
            require_qualitative = bool(task.evaluation_rubric)

        binary_res = self.binary.evaluate(task, artifacts, agent_output, workspace_path)
        constraint_res = self.constraints.evaluate(task, artifacts, agent_output, workspace_path)

        qualitative_res = None
        if task.evaluation_rubric and require_qualitative:
            qualitative_res = self.qualitative.evaluate(task, artifacts, agent_output, workspace_path)

        scores = [binary_res.overall_score, constraint_res.overall_score]
        if qualitative_res:
            scores.append(qualitative_res.overall_score)

        overall = sum(scores) / len(scores) if scores else 0.0
        overall_min = policy.get("overall_min_score")

        binary_ok = (not require_binary) or binary_res.is_complete
        constraints_ok = (not require_constraints) or constraint_res.is_complete

        qual_ok = True
        if require_qualitative:
            if qualitative_res:
                qual_min = float(policy.get("qualitative_min_score") or 0.0)
                qual_ok = qualitative_res.overall_score >= qual_min
                qualitative_res.is_complete = qual_ok
            else:
                qual_ok = False

        is_complete = binary_ok and constraints_ok and qual_ok
        if overall_min is not None:
            is_complete = is_complete and overall >= float(overall_min)

        confidence = qualitative_res.confidence if qualitative_res else binary_res.confidence

        missing = []
        suggested = []
        if require_binary:
            missing.extend(binary_res.missing_elements)
            suggested.extend(binary_res.suggested_actions)
        if require_constraints:
            missing.extend(constraint_res.missing_elements)
            suggested.extend(constraint_res.suggested_actions)
        if require_qualitative:
            if not qualitative_res:
                missing.append("Qualitative rubric missing")
                suggested.append("Add evaluation rubric or disable qualitative requirement")
            else:
                if not qualitative_res.is_complete and not qualitative_res.missing_elements:
                    missing.append(
                        f"Qualitative score {qualitative_res.overall_score:.2f} below threshold"
                    )
                missing.extend(qualitative_res.missing_elements)
                suggested.extend(qualitative_res.suggested_actions)

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


def create_default_evaluator(
    llm_client: Any,
    state_manager=None,
    model: str = "claude-sonnet-4-20250514",
    evaluation_policy: Optional[Dict[str, Any]] = None,
) -> CompositeEvaluator:
    return CompositeEvaluator(
        llm_client, state_manager, model=model, evaluation_policy=evaluation_policy
    )


def quick_evaluate(
    task: Task,
    artifacts: List[Artifact],
    agent_output: str,
    workspace_path: Path,
    llm_client: Any,
    model: str = "claude-sonnet-4-20250514",
    evaluation_policy: Optional[Dict[str, Any]] = None,
) -> EvaluationResult:
    evaluator = CompositeEvaluator(llm_client, model=model, evaluation_policy=evaluation_policy)
    return evaluator.evaluate(task, artifacts, agent_output, workspace_path)
