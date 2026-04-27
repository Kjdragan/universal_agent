"""
health_evaluator.py — LLM-powered evaluator for the heartbeat proactive advisor cycle.

This module intercepts the deterministic snapshot created by `build_morning_report()`,
compares it against the `Health_Checks_Lessons_Learned.md` document, and distills 
it into actionable, noise-free directives for Simone using standard `litellm` calls.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

import litellm

from universal_agent.utils.model_resolution import resolve_sonnet

logger = logging.getLogger(__name__)

def _get_lessons_learned_content() -> str:
    """Read the contents of the Health_Checks_Lessons_Learned.md document."""
    try:
        # Assuming we are running inside the UA directory structure
        project_root = Path(__file__).parent.parent.parent.parent
        doc_path = project_root / "docs" / "03_Operations" / "Health_Checks_Lessons_Learned.md"
        if doc_path.exists():
            return doc_path.read_text(encoding="utf-8")
        return "No lessons learned document found."
    except Exception as exc:
        logger.error(f"Failed to read lessons learned document: {exc}")
        return "Error reading lessons learned."

def _build_evaluation_prompt(raw_report_text: str, lessons_learned: str) -> str:
    """Construct the prompt sent to the LLM evaluator."""
    return f"""You are the Universal Agent Health Check Evaluator.
Your job is to read the deterministic task snapshot below, compare it against our Lessons Learned rules, and output a structured JSON response.

Here is the deterministic snapshot of the system's active queue and stale tasks:
-------------------------------------
{raw_report_text}
-------------------------------------

Here are the Health Checks Lessons Learned (rules for false positives and known incident mitigations):
-------------------------------------
{lessons_learned}
-------------------------------------

INSTRUCTIONS:
1. Review the snapshot. Ignore items that are explicitly listed as false positives or "operating normally" in the Lessons Learned.
2. For items that are genuinely stuck or require action (e.g., stale tasks, unhandled questions), formulate concise instructions (simone_directives) for the orchestrator agent "Simone" to self-heal the issue (e.g., "Review task X, it has been stuck in in_progress for 4 days, synthesise a completion note and close it").
3. If an item matches the Escalation Criteria and requires human intervention (like repetitive crashing or API token drops), emit a human_escalation message.
4. Output strict JSON with three keys:
   - "ignore": [] (List of string reasons for why things were ignored)
   - "simone_directives": [] (List of string directives)
   - "human_escalations": [] (List of string escalations)
   
Respond ONLY with the raw JSON object. Do not wrap it in markdown block quotes (```json).
"""

async def evaluate_health_snapshot(raw_report_dict: dict[str, Any]) -> Dict[str, Any]:
    """
    Asynchronously evaluates the raw morning report using an LLM to produce structured directives.
    Returns:
        dict with keys: ignore, simone_directives, human_escalations
    """
    raw_report_text = raw_report_dict.get("report_text", "")
    
    # If the report indicates zero active or stale tasks, bypass the LLM entirely to save tokens.
    stale_brainstorm_count = raw_report_dict.get("stale_brainstorm_count", 0)
    stale_in_progress = len(raw_report_dict.get("stale_in_progress", []))
    overdue_scheduled = len(raw_report_dict.get("overdue_scheduled", []))
    expiring_count = raw_report_dict.get("expiring_questions_count", 0)
    
    if stale_brainstorm_count == 0 and stale_in_progress == 0 and overdue_scheduled == 0 and expiring_count == 0:
        return {
            "ignore": ["No anomalous or stale items detected."],
            "simone_directives": [],
            "human_escalations": []
        }

    lessons_learned = _get_lessons_learned_content()
    prompt = _build_evaluation_prompt(raw_report_text, lessons_learned)
    
    model = resolve_sonnet()
    try:
        response = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=1000,
        )
        
        raw_output = response.choices[0].message.content.strip()
        
        # Cleanup markdown formatting if the model still wrapped it
        if raw_output.startswith("```json"):
            raw_output = raw_output[7:]
        if raw_output.startswith("```"):
            raw_output = raw_output[3:]
        if raw_output.endswith("```"):
            raw_output = raw_output[:-3]
            
        parsed = json.loads(raw_output.strip())
        
        return {
            "ignore": parsed.get("ignore", []),
            "simone_directives": parsed.get("simone_directives", []),
            "human_escalations": parsed.get("human_escalations", [])
        }
    except Exception as exc:
        logger.error(f"Health Evaluator LLM call failed: {exc}", exc_info=True)
        # Fallback to a safe empty structure
        return {
            "ignore": [],
            "simone_directives": ["Error running health evaluator. Please manually review the raw report."],
            "human_escalations": [f"Health evaluation failure: {exc}"]
        }
