print("Checking imports...")
from universal_agent import task_hub
from universal_agent.services.proactive_advisor import build_morning_report
from universal_agent.services.reflection_engine import (
    _get_nightly_task_count,
    _get_open_task_count,
    _get_stalled_brainstorms,
)
print("Finished!")
