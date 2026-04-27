import argparse
import asyncio
import logging
import os
from pathlib import Path
import sqlite3
import sys

try:
    from universal_agent.durable.db import get_activity_db_path
    from universal_agent.services.health_evaluator import evaluate_health_snapshot
    from universal_agent.services.proactive_advisor import build_morning_report
except ImportError:
    # Handle direct script execution vs module execution paths
    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root / "src"))
    from universal_agent.durable.db import get_activity_db_path
    from universal_agent.services.health_evaluator import evaluate_health_snapshot
    from universal_agent.services.proactive_advisor import build_morning_report

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def run_standalone_snapshot():
    """Generates the deterministic proactive task report explicitly on-demand."""
    db_path = get_activity_db_path()
    if not os.path.exists(db_path):
        logger.error(f"Activity DB not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        logger.info("Building deterministic report...")
        report = build_morning_report(conn)
        _raw_morning_text = str(report.get("report_text") or "")
        
        eval_result = {}
        if _raw_morning_text:
            logger.info("Evaluating health snapshot via LLM...")
            try:
                eval_result = await evaluate_health_snapshot(report)
            except Exception as e:
                logger.error(f"Failed to evaluate health snapshot: {e}")

        # Get capacity info
        max_coder = os.getenv("UA_MAX_CONCURRENT_VP_CODER", "1")
        max_general = os.getenv("UA_MAX_CONCURRENT_VP_GENERAL", "2")
        
        active_missions = []
        try:
            rows = conn.execute("SELECT task_id, title FROM task_hub_items WHERE status = 'delegated'").fetchall()
            for r in rows:
                tid = r["task_id"] if hasattr(r, "keys") else r[0]
                ttitle = r["title"] if hasattr(r, "keys") else r[1]
                active_missions.append(f"[{tid}] {ttitle}")
        except Exception as e:
            logger.debug("Failed to list active missions for capacity report: %s", e)

        _cap_report = "== CAPACITY REPORT ==\n"
        _cap_report += f"Max Concurrent VP Coder: {max_coder}\n"
        _cap_report += f"Max Concurrent VP General: {max_general}\n"
        _cap_report += f"Active VP Missions ({len(active_missions)}):\n"
        for m in active_missions:
            _cap_report += f"- {m}\n"
            
        dirs = eval_result.get("simone_directives", [])
        esc = eval_result.get("human_escalations", [])

        _morning_text = _raw_morning_text + "\n\n" + _cap_report + "\n"
        if dirs or esc:
            _morning_text += "== HEALTH CHECK DIRECTIVES ==\n"
            for d in dirs:
                _morning_text += f"- {d}\n"
            if esc:
                _morning_text += "\n== ESCALATIONS ==\n"
                for e in esc:
                    _morning_text += f"- {e}\n"
        else:
            _morning_text += "== HEALTH CHECK ==\nAll systems nominal. No stuck tasks."

        # Write to file
        project_root = Path(__file__).parent.parent.parent.parent
        artifacts_dir = project_root / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)
        report_path = artifacts_dir / "morning_report_latest.md"
        
        report_path.write_text(_morning_text, encoding="utf-8")
        logger.info(f"Health snapshot successfully written to {report_path.resolve()}")

    finally:
        conn.close()

if __name__ == "__main__":
    asyncio.run(run_standalone_snapshot())
