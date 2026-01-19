"""
URW Plan Persistence

Dual persistence for plans: JSON files for portability, SQLite for querying.
Based on interview.md design.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from .plan_schema import AtomicTask, Plan, Phase, TaskStatus


class PlanPersistence:
    """JSON file-based plan persistence for portability."""
    
    def __init__(self, storage_dir: Path):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
    
    def save_plan(self, plan: Plan) -> Path:
        """Save plan to JSON file."""
        filepath = self.storage_dir / f"plan_{plan.id}.json"
        filepath.write_text(plan.model_dump_json(indent=2))
        return filepath
    
    def load_plan(self, filepath: Path) -> Plan:
        """Load plan from JSON file."""
        return Plan.model_validate_json(filepath.read_text())
    
    def load_by_id(self, plan_id: str) -> Optional[Plan]:
        """Load plan by ID."""
        filepath = self.storage_dir / f"plan_{plan_id}.json"
        if filepath.exists():
            return self.load_plan(filepath)
        return None
    
    def list_plans(self) -> List[Path]:
        """List all saved plan files."""
        return list(self.storage_dir.glob("plan_*.json"))


class SQLitePlanStore:
    """SQLite-based plan persistence for efficient querying."""
    
    def __init__(self, db_path: str = "plans.db"):
        self.db_path = db_path
        self._init_schema()
    
    def _init_schema(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    massive_request TEXT,
                    data TEXT NOT NULL,  -- Full JSON
                    status TEXT DEFAULT 'pending',
                    harness_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                
                CREATE TABLE IF NOT EXISTS phases (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    phase_order INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    session_path TEXT,
                    FOREIGN KEY (plan_id) REFERENCES plans(id)
                );
                
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    phase_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    priority TEXT DEFAULT 'medium',
                    dependencies TEXT,  -- JSON array of task IDs
                    FOREIGN KEY (plan_id) REFERENCES plans(id),
                    FOREIGN KEY (phase_id) REFERENCES phases(id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
                CREATE INDEX IF NOT EXISTS idx_phases_plan ON phases(plan_id);
                CREATE INDEX IF NOT EXISTS idx_plans_harness ON plans(harness_id);
            """)
    
    def save_plan(self, plan: Plan) -> None:
        """Save plan and all its phases/tasks to database."""
        with sqlite3.connect(self.db_path) as conn:
            # Save plan
            conn.execute("""
                INSERT OR REPLACE INTO plans 
                (id, name, description, massive_request, data, status, harness_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(plan.id), 
                plan.name, 
                plan.description,
                plan.massive_request,
                plan.model_dump_json(),
                plan.status.value, 
                plan.harness_id,
                plan.created_at.isoformat(), 
                plan.updated_at.isoformat()
            ))
            
            # Save phases
            for phase in plan.phases:
                conn.execute("""
                    INSERT OR REPLACE INTO phases 
                    (id, plan_id, name, phase_order, status, session_path)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    str(phase.id),
                    str(plan.id),
                    phase.name,
                    phase.order,
                    phase.status.value,
                    str(phase.session_path) if phase.session_path else None
                ))
                
                # Save tasks
                for task in phase.tasks:
                    conn.execute("""
                        INSERT OR REPLACE INTO tasks 
                        (id, plan_id, phase_id, name, status, priority, dependencies)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(task.id),
                        str(plan.id),
                        str(phase.id),
                        task.name,
                        task.status.value,
                        task.priority.value,
                        json.dumps([str(d) for d in task.dependencies])
                    ))
    
    def load_plan(self, plan_id: str) -> Optional[Plan]:
        """Load plan by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT data FROM plans WHERE id = ?", 
                (plan_id,)
            ).fetchone()
            
            if row:
                return Plan.model_validate_json(row["data"])
            return None
    
    def load_by_harness_id(self, harness_id: str) -> Optional[Plan]:
        """Load plan by harness ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT data FROM plans WHERE harness_id = ?", 
                (harness_id,)
            ).fetchone()
            
            if row:
                return Plan.model_validate_json(row["data"])
            return None
    
    def update_phase_status(self, phase_id: str, status: TaskStatus, session_path: Optional[str] = None) -> None:
        """Update phase status and optionally session path."""
        with sqlite3.connect(self.db_path) as conn:
            if session_path:
                conn.execute(
                    "UPDATE phases SET status = ?, session_path = ? WHERE id = ?",
                    (status.value, session_path, phase_id)
                )
            else:
                conn.execute(
                    "UPDATE phases SET status = ? WHERE id = ?",
                    (status.value, phase_id)
                )
    
    def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        """Update task status."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?",
                (status.value, task_id)
            )
    
    def get_pending_tasks(self, plan_id: str) -> List[dict]:
        """Get tasks ready for execution (all dependencies completed)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT t.* FROM tasks t
                WHERE t.plan_id = ? AND t.status = 'pending'
                AND NOT EXISTS (
                    SELECT 1 FROM tasks dep 
                    WHERE dep.id IN (SELECT value FROM json_each(t.dependencies))
                    AND dep.status != 'completed'
                )
            """, (plan_id,)).fetchall()
            
            return [dict(row) for row in rows]
    
    def get_plan_summary(self, plan_id: str) -> dict:
        """Get summary stats for a plan."""
        with sqlite3.connect(self.db_path) as conn:
            phases_total = conn.execute(
                "SELECT COUNT(*) FROM phases WHERE plan_id = ?", (plan_id,)
            ).fetchone()[0]
            
            phases_complete = conn.execute(
                "SELECT COUNT(*) FROM phases WHERE plan_id = ? AND status = 'completed'", 
                (plan_id,)
            ).fetchone()[0]
            
            tasks_total = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE plan_id = ?", (plan_id,)
            ).fetchone()[0]
            
            tasks_complete = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE plan_id = ? AND status = 'completed'", 
                (plan_id,)
            ).fetchone()[0]
            
            return {
                "phases_total": phases_total,
                "phases_complete": phases_complete,
                "tasks_total": tasks_total,
                "tasks_complete": tasks_complete,
                "progress_pct": (tasks_complete / tasks_total * 100) if tasks_total > 0 else 0
            }
