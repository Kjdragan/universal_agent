#!/usr/bin/env python3
"""
Test script for PhasePlanner

Tests that the PhasePlanner correctly groups tasks into phases.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from universal_agent.urw.phase_planner import Phase, PhaseStatus, PhasePlanner
from universal_agent.urw.state import Task, TaskStatus


def create_mock_tasks(template: str = "research_report") -> list:
    """Create mock tasks simulating decomposition output."""
    
    if template == "research_report":
        return [
            Task(
                id="task_scope",
                title="Define research scope and questions",
                description="Identify key research questions and scope.",
                depends_on=[],
                status=TaskStatus.PENDING,
            ),
            Task(
                id="task_gather",
                title="Gather information from sources",
                description="Search and collect relevant information.",
                depends_on=["task_scope"],
                status=TaskStatus.PENDING,
            ),
            Task(
                id="task_analyze",
                title="Analyze and synthesize findings",
                description="Analyze gathered data and extract insights.",
                depends_on=["task_gather"],
                status=TaskStatus.PENDING,
            ),
            Task(
                id="task_report",
                title="Write final report",
                description="Create comprehensive report with findings.",
                depends_on=["task_analyze"],
                status=TaskStatus.PENDING,
            ),
        ]
    
    elif template == "email_with_research":
        return [
            Task(
                id="task_research",
                title="Research topic",
                description="Gather information.",
                depends_on=[],
                status=TaskStatus.PENDING,
            ),
            Task(
                id="task_analyze",
                title="Analyze findings",
                description="Synthesize research.",
                depends_on=["task_research"],
                status=TaskStatus.PENDING,
            ),
            Task(
                id="task_draft",
                title="Draft email",
                description="Write email content.",
                depends_on=["task_analyze"],
                status=TaskStatus.PENDING,
            ),
            Task(
                id="task_send",
                title="Send email",
                description="Send the email via Gmail.",
                depends_on=["task_draft"],
                status=TaskStatus.PENDING,
            ),
        ]
    
    elif template == "complex_multi_deliverable":
        # More complex scenario with multiple deliverables
        return [
            Task(id="t1", title="Research phase 1", description="Initial research", depends_on=[], status=TaskStatus.PENDING),
            Task(id="t2", title="Research phase 2", description="Deep dive", depends_on=["t1"], status=TaskStatus.PENDING),
            Task(id="t3", title="Analyze data", description="Analyze all research", depends_on=["t1", "t2"], status=TaskStatus.PENDING),
            Task(id="t4", title="Create report", description="Write detailed report", depends_on=["t3"], status=TaskStatus.PENDING),
            Task(id="t5", title="Create presentation", description="Build slide deck", depends_on=["t3"], status=TaskStatus.PENDING),
            Task(id="t6", title="Send report email", description="Email the report", depends_on=["t4"], status=TaskStatus.PENDING),
            Task(id="t7", title="Upload to drive", description="Upload deliverables", depends_on=["t4", "t5"], status=TaskStatus.PENDING),
        ]
    
    return []


def test_single_phase_simple():
    """Test that simple 4-task chain becomes single phase."""
    print("=" * 60)
    print("Test 1: Simple research_report → Single Phase")
    print("=" * 60)
    
    tasks = create_mock_tasks("research_report")
    planner = PhasePlanner()
    phases = planner.plan_phases(tasks)
    
    print(f"Tasks: {len(tasks)}")
    print(f"Phases: {len(phases)}")
    
    assert len(phases) == 1, f"Expected 1 phase, got {len(phases)}"
    assert len(phases[0].task_ids) == 4, f"Expected 4 tasks in phase, got {len(phases[0].task_ids)}"
    
    print(f"✅ Single phase: {phases[0].name}")
    print(f"   Tasks: {phases[0].task_ids}")
    print()


def test_single_phase_forced():
    """Test single_phase_mode=True forces everything into one phase."""
    print("=" * 60)
    print("Test 2: Forced Single Phase Mode")
    print("=" * 60)
    
    tasks = create_mock_tasks("complex_multi_deliverable")
    planner = PhasePlanner()
    
    # Without forcing
    phases_normal = planner.plan_phases(tasks, single_phase_mode=False)
    # With forcing
    phases_forced = planner.plan_phases(tasks, single_phase_mode=True)
    
    print(f"Normal planning: {len(phases_normal)} phase(s)")
    print(f"Forced single phase: {len(phases_forced)} phase(s)")
    
    assert len(phases_forced) == 1, "Forced single phase should produce 1 phase"
    assert len(phases_forced[0].task_ids) == 7, "All 7 tasks should be in single phase"
    
    print(f"✅ Single forced phase contains all {len(phases_forced[0].task_ids)} tasks")
    print()


def test_complexity_estimation():
    """Test complexity estimation."""
    print("=" * 60)
    print("Test 3: Complexity Estimation")
    print("=" * 60)
    
    planner = PhasePlanner()
    
    simple_tasks = create_mock_tasks("research_report")
    complex_tasks = create_mock_tasks("complex_multi_deliverable")
    
    simple_complexity = planner.estimate_complexity(simple_tasks)
    complex_complexity = planner.estimate_complexity(complex_tasks)
    
    print(f"4-task research: {simple_complexity}")
    print(f"7-task multi-deliverable: {complex_complexity}")
    
    assert simple_complexity == "simple", f"Expected 'simple', got {simple_complexity}"
    assert complex_complexity == "complex", f"Expected 'complex', got {complex_complexity}"
    
    print(f"✅ Complexity estimation working correctly")
    print()


def test_phase_boundary_detection():
    """Test that natural boundaries are detected."""
    print("=" * 60)
    print("Test 4: Phase Boundary Detection")
    print("=" * 60)
    
    tasks = create_mock_tasks("email_with_research")
    planner = PhasePlanner()
    
    # Check boundary detection
    for task in tasks:
        is_boundary = planner._is_phase_boundary(task)
        boundary_marker = "⬆️ BOUNDARY" if is_boundary else ""
        print(f"  {task.title}: {boundary_marker}")
    
    # The "send" task should be a boundary
    send_task = next(t for t in tasks if "send" in t.title.lower())
    analyze_task = next(t for t in tasks if "analyze" in t.title.lower())
    
    assert planner._is_phase_boundary(send_task), "Send task should be boundary"
    assert planner._is_phase_boundary(analyze_task), "Analyze task should be boundary"
    
    print(f"✅ Boundaries detected correctly")
    print()


def test_phase_status_tracking():
    """Test Phase status tracking."""
    print("=" * 60)
    print("Test 5: Phase Status Tracking")
    print("=" * 60)
    
    phase = Phase(
        phase_id="test_phase",
        name="Test Phase",
        task_ids=["t1", "t2", "t3"],
    )
    
    print(f"Initial status: {phase.status.value}")
    assert phase.status == PhaseStatus.PENDING
    
    phase.mark_started()
    print(f"After start: {phase.status.value}")
    assert phase.status == PhaseStatus.IN_PROGRESS
    assert phase.started_at is not None
    
    phase.mark_complete(completed_ids=["t1", "t2", "t3"])
    print(f"After complete: {phase.status.value}")
    assert phase.status == PhaseStatus.COMPLETE
    assert phase.completed_at is not None
    
    print(f"✅ Status tracking working correctly")
    print()


def test_multi_phase_complex():
    """Test multi-phase planning for complex tasks."""
    print("=" * 60)
    print("Test 6: Multi-Phase Planning (Complex)")
    print("=" * 60)
    
    tasks = create_mock_tasks("complex_multi_deliverable")
    planner = PhasePlanner(max_tasks_per_phase=3)  # Force smaller phases
    phases = planner.plan_phases(tasks)
    
    print(f"Tasks: {len(tasks)}")
    print(f"Phases: {len(phases)}")
    
    for phase in phases:
        print(f"\n  {phase.name}")
        print(f"    Tasks: {phase.task_ids}")
    
    # Should have multiple phases
    assert len(phases) >= 2, f"Expected at least 2 phases for complex task"
    
    # All tasks should be covered
    all_task_ids = set()
    for phase in phases:
        all_task_ids.update(phase.task_ids)
    
    expected_ids = {t.id for t in tasks}
    assert all_task_ids == expected_ids, "Not all tasks covered by phases"
    
    print(f"\n✅ Multi-phase planning working correctly")
    print()


def main():
    print()
    print("🧪 PhasePlanner Test Suite")
    print("=" * 60)
    print()
    
    test_single_phase_simple()
    test_single_phase_forced()
    test_complexity_estimation()
    test_phase_boundary_detection()
    test_phase_status_tracking()
    test_multi_phase_complex()
    
    print("=" * 60)
    print("✅ All PhasePlanner tests passed!")
    print("=" * 60)
    print()
    print("Key findings:")
    print("- Simple 4-task chains → single phase (fixes over-decomposition)")
    print("- Complex multi-deliverable tasks → appropriate multi-phase")
    print("- Natural boundaries (send, analyze) are detected")
    print("- single_phase_mode=True forces single phase for simple queries")
    print()


if __name__ == "__main__":
    main()
