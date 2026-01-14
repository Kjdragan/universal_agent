"""
Universal Ralph Wrapper - Completion Evaluator

Evaluates whether tasks are complete using multiple strategies:
1. Binary checks (file exists, API returned 200)
2. Constraint validation (min length, contains text)
3. LLM-as-judge qualitative evaluation
4. Composite evaluation combining all strategies

Unlike code-based Ralph which uses test pass/fail, universal tasks
require fuzzy evaluation with confidence scoring.
"""

import re
import json
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from enum import Enum

from urw_state import (
    Task, Artifact, CompletionConfidence, ArtifactType
)


# =============================================================================
# EVALUATION RESULT
# =============================================================================

@dataclass
class EvaluationResult:
    """Result of evaluating task completion."""
    
    is_complete: bool
    confidence: CompletionConfidence
    overall_score: float  # 0.0 to 1.0
    
    # Detailed breakdown
    binary_results: Dict[str, bool] = field(default_factory=dict)
    constraint_results: Dict[str, Tuple[bool, str]] = field(default_factory=dict)
    qualitative_score: Optional[float] = None
    qualitative_reasoning: Optional[str] = None
    
    # Guidance for next iteration
    missing_elements: List[str] = field(default_factory=list)
    suggested_actions: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "is_complete": self.is_complete,
            "confidence": self.confidence.value,
            "overall_score": self.overall_score,
            "binary_results": self.binary_results,
            "constraint_results": {
                k: {"passed": v[0], "message": v[1]} 
                for k, v in self.constraint_results.items()
            },
            "qualitative_score": self.qualitative_score,
            "qualitative_reasoning": self.qualitative_reasoning,
            "missing_elements": self.missing_elements,
            "suggested_actions": self.suggested_actions,
        }


# =============================================================================
# EVALUATOR INTERFACE
# =============================================================================

class Evaluator(ABC):
    """Abstract base class for completion evaluators."""
    
    @abstractmethod
    def evaluate(self, task: Task, artifacts: List[Artifact], 
                agent_output: str, workspace_path: Path) -> EvaluationResult:
        """
        Evaluate whether a task is complete.
        
        Args:
            task: The task being evaluated
            artifacts: Artifacts produced during execution
            agent_output: The agent's final output text
            workspace_path: Path to workspace for file checks
        
        Returns:
            EvaluationResult with completion status and details
        """
        pass


# =============================================================================
# BINARY CHECK EVALUATOR
# =============================================================================

class BinaryCheckEvaluator(Evaluator):
    """
    Evaluates binary checks: file exists, side effect recorded, etc.
    These are deterministic pass/fail checks.
    """
    
    def __init__(self, state_manager=None):
        """
        Args:
            state_manager: URWStateManager for checking side effects
        """
        self.state_manager = state_manager
    
    def evaluate(self, task: Task, artifacts: List[Artifact],
                agent_output: str, workspace_path: Path) -> EvaluationResult:
        """Evaluate all binary checks for the task."""
        
        if not task.binary_checks:
            return EvaluationResult(
                is_complete=True,
                confidence=CompletionConfidence.DEFINITIVE,
                overall_score=1.0,
            )
        
        results = {}
        missing = []
        
        for check in task.binary_checks:
            passed, message = self._evaluate_check(
                check, artifacts, workspace_path
            )
            results[check] = passed
            if not passed:
                missing.append(message)
        
        all_passed = all(results.values())
        score = sum(results.values()) / len(results) if results else 1.0
        
        return EvaluationResult(
            is_complete=all_passed,
            confidence=CompletionConfidence.DEFINITIVE if all_passed else CompletionConfidence.FAILED,
            overall_score=score,
            binary_results=results,
            missing_elements=missing,
            suggested_actions=[f"Complete: {m}" for m in missing],
        )
    
    def _evaluate_check(self, check: str, artifacts: List[Artifact],
                        workspace_path: Path) -> Tuple[bool, str]:
        """Evaluate a single binary check."""
        
        if check.startswith("file_exists:"):
            filename = check.split(":", 1)[1]
            # Check in artifacts directory
            artifacts_path = workspace_path / '.urw' / 'artifacts' / filename
            if artifacts_path.exists():
                return True, ""
            # Check in workspace root
            if (workspace_path / filename).exists():
                return True, ""
            # Check if any artifact has this filename
            for art in artifacts:
                if art.file_path and art.file_path.endswith(filename):
                    return True, ""
            return False, f"File '{filename}' not found"
        
        elif check.startswith("artifact_exists:"):
            artifact_id = check.split(":", 1)[1]
            for art in artifacts:
                if art.id == artifact_id:
                    return True, ""
            return False, f"Artifact '{artifact_id}' not produced"
        
        elif check.startswith("side_effect:"):
            effect_type = check.split(":", 1)[1]
            if self.state_manager:
                # Check if any side effect of this type was recorded
                effects = self.state_manager.conn.execute(
                    "SELECT id FROM side_effects WHERE effect_type = ?",
                    (effect_type,)
                ).fetchone()
                if effects:
                    return True, ""
            return False, f"Side effect '{effect_type}' not recorded"
        
        elif check.startswith("contains:"):
            # Check if agent output contains specific text
            text = check.split(":", 1)[1]
            if text.lower() in agent_output.lower():
                return True, ""
            return False, f"Output doesn't contain '{text}'"
        
        else:
            # Unknown check type - pass by default with warning
            return True, f"Unknown check type: {check}"


# =============================================================================
# CONSTRAINT EVALUATOR
# =============================================================================

class ConstraintEvaluator(Evaluator):
    """
    Evaluates constraint checks: min/max length, contains patterns, etc.
    These are rule-based validations.
    """
    
    def evaluate(self, task: Task, artifacts: List[Artifact],
                agent_output: str, workspace_path: Path) -> EvaluationResult:
        """Evaluate all constraints for the task."""
        
        if not task.constraints:
            return EvaluationResult(
                is_complete=True,
                confidence=CompletionConfidence.DEFINITIVE,
                overall_score=1.0,
            )
        
        results = {}
        missing = []
        
        # Get primary artifact content for constraint checks
        content = self._get_primary_content(artifacts, workspace_path)
        if content is None:
            content = agent_output
        
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
    
    def _get_primary_content(self, artifacts: List[Artifact],
                             workspace_path: Path) -> Optional[str]:
        """Get content from the primary artifact for constraint checking."""
        for artifact in artifacts:
            if artifact.artifact_type == ArtifactType.FILE and artifact.file_path:
                file_path = workspace_path / '.urw' / 'artifacts' / artifact.file_path
                if file_path.exists():
                    try:
                        return file_path.read_text()
                    except:
                        pass
        return None
    
    def _evaluate_constraint(self, constraint: Dict, 
                            content: str) -> Tuple[bool, str]:
        """Evaluate a single constraint."""
        
        ctype = constraint.get('type', '')
        value = constraint.get('value')
        
        if ctype == 'min_length':
            actual = len(content)
            if actual >= value:
                return True, ""
            return False, f"Content length {actual} < minimum {value}"
        
        elif ctype == 'max_length':
            actual = len(content)
            if actual <= value:
                return True, ""
            return False, f"Content length {actual} > maximum {value}"
        
        elif ctype == 'min_words':
            actual = len(content.split())
            if actual >= value:
                return True, ""
            return False, f"Word count {actual} < minimum {value}"
        
        elif ctype == 'contains':
            if value.lower() in content.lower():
                return True, ""
            return False, f"Content doesn't contain required text: '{value}'"
        
        elif ctype == 'not_contains':
            if value.lower() not in content.lower():
                return True, ""
            return False, f"Content contains forbidden text: '{value}'"
        
        elif ctype == 'regex_match':
            if re.search(value, content):
                return True, ""
            return False, f"Content doesn't match pattern: {value}"
        
        elif ctype == 'json_valid':
            try:
                json.loads(content)
                return True, ""
            except:
                return False, "Content is not valid JSON"
        
        elif ctype == 'has_sections':
            # Check for required section headers
            sections = value if isinstance(value, list) else [value]
            missing_sections = []
            for section in sections:
                if section.lower() not in content.lower():
                    missing_sections.append(section)
            if not missing_sections:
                return True, ""
            return False, f"Missing sections: {', '.join(missing_sections)}"
        
        else:
            # Unknown constraint type - pass with warning
            return True, f"Unknown constraint type: {ctype}"


# =============================================================================
# LLM JUDGE EVALUATOR
# =============================================================================

class LLMJudgeEvaluator(Evaluator):
    """
    Uses LLM-as-judge for qualitative evaluation.
    Essential for tasks where success is subjective.
    """
    
    def __init__(self, llm_client: Any, model: str = "claude-sonnet-4-20250514"):
        """
        Args:
            llm_client: Anthropic client or compatible interface
            model: Model to use for evaluation
        """
        self.llm_client = llm_client
        self.model = model
    
    def evaluate(self, task: Task, artifacts: List[Artifact],
                agent_output: str, workspace_path: Path) -> EvaluationResult:
        """Use LLM to evaluate task completion qualitatively."""
        
        if not task.evaluation_rubric:
            # No rubric = skip qualitative evaluation
            return EvaluationResult(
                is_complete=True,
                confidence=CompletionConfidence.MEDIUM,
                overall_score=0.7,  # Neutral score when no rubric
            )
        
        # Build evaluation prompt
        prompt = self._build_evaluation_prompt(task, artifacts, 
                                               agent_output, workspace_path)
        
        response = self.llm_client.messages.create(
            model=self.model,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return self._parse_evaluation_response(response.content[0].text, task)
    
    def _build_evaluation_prompt(self, task: Task, artifacts: List[Artifact],
                                 agent_output: str, 
                                 workspace_path: Path) -> str:
        """Build prompt for LLM evaluation."""
        
        # Get artifact contents for evaluation
        artifact_contents = []
        for artifact in artifacts:
            if artifact.artifact_type == ArtifactType.FILE and artifact.file_path:
                file_path = workspace_path / '.urw' / 'artifacts' / artifact.file_path
                if file_path.exists():
                    try:
                        content = file_path.read_text()
                        # Truncate if too long
                        if len(content) > 10000:
                            content = content[:5000] + "\n\n[... truncated ...]\n\n" + content[-2000:]
                        artifact_contents.append(f"**{artifact.file_path}:**\n```\n{content}\n```")
                    except:
                        artifact_contents.append(f"**{artifact.file_path}:** [Unable to read]")
        
        return f"""You are an expert evaluator assessing whether a task has been completed successfully.

## Task
**Title:** {task.title}
**Description:** {task.description}

## Evaluation Rubric
{task.evaluation_rubric}

## Minimum Acceptable Score
{task.minimum_acceptable_score} (on a 0-1 scale)

## Agent's Output
{agent_output[:5000] if len(agent_output) > 5000 else agent_output}

## Artifacts Produced
{chr(10).join(artifact_contents) if artifact_contents else "No artifacts produced."}

## Your Evaluation
Please evaluate the task completion and respond with a JSON object:

```json
{{
    "overall_score": <float 0.0-1.0>,
    "is_complete": <boolean>,
    "reasoning": "<your detailed reasoning>",
    "missing_elements": ["<element 1>", "<element 2>"],
    "suggested_actions": ["<action 1>", "<action 2>"],
    "strengths": ["<strength 1>", "<strength 2>"]
}}
```

Be fair but rigorous. Consider:
- Does the output address all aspects of the task?
- Is the quality acceptable for the intended purpose?
- Are there critical gaps or errors?
- Would this need significant revision before being useful?

Return ONLY the JSON object."""
    
    def _parse_evaluation_response(self, response: str, 
                                   task: Task) -> EvaluationResult:
        """Parse LLM evaluation response."""
        
        # Extract JSON from response
        json_str = response.strip()
        if "```json" in json_str:
            start = json_str.find("```json") + 7
            end = json_str.find("```", start)
            json_str = json_str[start:end].strip()
        elif "```" in json_str:
            start = json_str.find("```") + 3
            end = json_str.find("```", start)
            json_str = json_str[start:end].strip()
        
        try:
            eval_data = json.loads(json_str)
        except json.JSONDecodeError:
            # Fallback if JSON parsing fails
            return EvaluationResult(
                is_complete=False,
                confidence=CompletionConfidence.UNCERTAIN,
                overall_score=0.5,
                qualitative_reasoning="Failed to parse evaluation response",
                missing_elements=["Unable to evaluate"],
                suggested_actions=["Retry evaluation"],
            )
        
        score = eval_data.get('overall_score', 0.5)
        is_complete = eval_data.get('is_complete', score >= task.minimum_acceptable_score)
        
        # Determine confidence from score
        if score >= 0.85:
            confidence = CompletionConfidence.HIGH
        elif score >= 0.6:
            confidence = CompletionConfidence.MEDIUM
        elif score >= 0.4:
            confidence = CompletionConfidence.LOW
        else:
            confidence = CompletionConfidence.UNCERTAIN
        
        return EvaluationResult(
            is_complete=is_complete,
            confidence=confidence,
            overall_score=score,
            qualitative_score=score,
            qualitative_reasoning=eval_data.get('reasoning', ''),
            missing_elements=eval_data.get('missing_elements', []),
            suggested_actions=eval_data.get('suggested_actions', []),
        )


# =============================================================================
# COMPOSITE EVALUATOR
# =============================================================================

class CompositeEvaluator(Evaluator):
    """
    Combines multiple evaluation strategies based on task verification type.
    This is the main evaluator used by the orchestrator.
    """
    
    def __init__(self, llm_client: Any = None, state_manager=None,
                 model: str = "claude-sonnet-4-20250514"):
        """
        Args:
            llm_client: Anthropic client (required for qualitative evaluation)
            state_manager: URWStateManager for side effect checks
            model: Model for LLM evaluation
        """
        self.binary_evaluator = BinaryCheckEvaluator(state_manager)
        self.constraint_evaluator = ConstraintEvaluator()
        self.llm_evaluator = LLMJudgeEvaluator(llm_client, model) if llm_client else None
        self.state_manager = state_manager
    
    def evaluate(self, task: Task, artifacts: List[Artifact],
                agent_output: str, workspace_path: Path) -> EvaluationResult:
        """
        Evaluate task using appropriate strategies based on verification_type.
        
        verification_type options:
        - "binary": Only binary checks
        - "constraint": Binary + constraint checks
        - "qualitative": LLM evaluation only
        - "composite": All strategies combined
        """
        
        vtype = task.verification_type
        
        if vtype == "binary":
            return self.binary_evaluator.evaluate(
                task, artifacts, agent_output, workspace_path
            )
        
        elif vtype == "constraint":
            binary_result = self.binary_evaluator.evaluate(
                task, artifacts, agent_output, workspace_path
            )
            constraint_result = self.constraint_evaluator.evaluate(
                task, artifacts, agent_output, workspace_path
            )
            return self._combine_results([binary_result, constraint_result], 
                                         strategy="all")
        
        elif vtype == "qualitative":
            if not self.llm_evaluator:
                raise ValueError("LLM client required for qualitative evaluation")
            return self.llm_evaluator.evaluate(
                task, artifacts, agent_output, workspace_path
            )
        
        else:  # "composite" or default
            results = []
            
            # Always run binary checks if defined
            if task.binary_checks:
                results.append(self.binary_evaluator.evaluate(
                    task, artifacts, agent_output, workspace_path
                ))
            
            # Run constraint checks if defined
            if task.constraints:
                results.append(self.constraint_evaluator.evaluate(
                    task, artifacts, agent_output, workspace_path
                ))
            
            # Run LLM evaluation if rubric defined and client available
            if task.evaluation_rubric and self.llm_evaluator:
                results.append(self.llm_evaluator.evaluate(
                    task, artifacts, agent_output, workspace_path
                ))
            
            if not results:
                # No checks defined - assume complete with medium confidence
                return EvaluationResult(
                    is_complete=True,
                    confidence=CompletionConfidence.MEDIUM,
                    overall_score=0.7,
                )
            
            return self._combine_results(results, strategy="weighted")
    
    def _combine_results(self, results: List[EvaluationResult],
                         strategy: str = "all") -> EvaluationResult:
        """
        Combine multiple evaluation results.
        
        Strategies:
        - "all": All must pass (AND logic)
        - "any": Any can pass (OR logic)
        - "weighted": Weighted average with binary checks as hard requirements
        """
        
        if not results:
            return EvaluationResult(
                is_complete=True,
                confidence=CompletionConfidence.UNCERTAIN,
                overall_score=0.5,
            )
        
        # Aggregate all results
        all_binary = {}
        all_constraints = {}
        all_missing = []
        all_actions = []
        qualitative_scores = []
        qualitative_reasoning = []
        
        for result in results:
            all_binary.update(result.binary_results)
            all_constraints.update(result.constraint_results)
            all_missing.extend(result.missing_elements)
            all_actions.extend(result.suggested_actions)
            if result.qualitative_score is not None:
                qualitative_scores.append(result.qualitative_score)
            if result.qualitative_reasoning:
                qualitative_reasoning.append(result.qualitative_reasoning)
        
        if strategy == "all":
            is_complete = all(r.is_complete for r in results)
            overall_score = min(r.overall_score for r in results)
        
        elif strategy == "any":
            is_complete = any(r.is_complete for r in results)
            overall_score = max(r.overall_score for r in results)
        
        else:  # "weighted"
            # Binary checks are hard requirements
            binary_passed = all(all_binary.values()) if all_binary else True
            constraint_passed = all(r[0] for r in all_constraints.values()) if all_constraints else True
            
            # Calculate weighted score
            scores = [r.overall_score for r in results]
            overall_score = sum(scores) / len(scores) if scores else 0.5
            
            # Binary failure = not complete regardless of score
            if not binary_passed:
                is_complete = False
            # Constraint failure with low score = not complete
            elif not constraint_passed and overall_score < 0.6:
                is_complete = False
            else:
                is_complete = overall_score >= 0.6
        
        # Determine confidence
        if all(r.confidence == CompletionConfidence.DEFINITIVE for r in results):
            confidence = CompletionConfidence.DEFINITIVE
        elif overall_score >= 0.85:
            confidence = CompletionConfidence.HIGH
        elif overall_score >= 0.6:
            confidence = CompletionConfidence.MEDIUM
        elif overall_score >= 0.4:
            confidence = CompletionConfidence.LOW
        else:
            confidence = CompletionConfidence.UNCERTAIN
        
        return EvaluationResult(
            is_complete=is_complete,
            confidence=confidence,
            overall_score=overall_score,
            binary_results=all_binary,
            constraint_results=all_constraints,
            qualitative_score=sum(qualitative_scores) / len(qualitative_scores) if qualitative_scores else None,
            qualitative_reasoning="\n\n".join(qualitative_reasoning) if qualitative_reasoning else None,
            missing_elements=list(set(all_missing)),  # Deduplicate
            suggested_actions=list(set(all_actions)),
        )


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def create_default_evaluator(llm_client: Any = None, 
                            state_manager=None) -> CompositeEvaluator:
    """Create a default composite evaluator with optional LLM support."""
    return CompositeEvaluator(
        llm_client=llm_client,
        state_manager=state_manager
    )


def quick_evaluate(task: Task, agent_output: str,
                  workspace_path: Path,
                  llm_client: Any = None) -> EvaluationResult:
    """
    Quick one-shot evaluation without full evaluator setup.
    Useful for simple cases.
    """
    evaluator = CompositeEvaluator(llm_client=llm_client)
    return evaluator.evaluate(task, [], agent_output, workspace_path)
