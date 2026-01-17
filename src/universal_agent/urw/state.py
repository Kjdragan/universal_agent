"""
Universal Ralph Wrapper - State Management

Deterministic state management using:
- SQLite: Structured relationships, queries, task graph
- Git: Checkpointing, history, rollback, diffing
- File System: Artifacts, human-readable progress, guardrails
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional


class TaskStatus(Enum):
    """Status of a task in the execution plan."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    NEEDS_REVIEW = "needs_review"
    COMPLETE = "complete"
    FAILED = "failed"


class CompletionConfidence(Enum):
    """How confident are we that a task is complete?"""

    DEFINITIVE = "definitive"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNCERTAIN = "uncertain"
    FAILED = "failed"


class ArtifactType(Enum):
    """Types of artifacts produced by tasks."""

    FILE = "file"
    METADATA = "metadata"
    SIDE_EFFECT = "side_effect"


@dataclass
class Task:
    """A single task in the execution plan."""

    id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    parent_task_id: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)

    verification_type: str = "composite"
    binary_checks: List[str] = field(default_factory=list)
    constraints: List[Dict[str, Any]] = field(default_factory=list)
    evaluation_rubric: Optional[str] = None
    minimum_acceptable_score: float = 0.7

    max_iterations: int = 10
    requires_tools: List[str] = field(default_factory=list)

    iteration_started: Optional[int] = None
    iteration_completed: Optional[int] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "parent_task_id": self.parent_task_id,
            "depends_on": self.depends_on,
            "verification_type": self.verification_type,
            "binary_checks": self.binary_checks,
            "constraints": self.constraints,
            "evaluation_rubric": self.evaluation_rubric,
            "minimum_acceptable_score": self.minimum_acceptable_score,
            "max_iterations": self.max_iterations,
            "requires_tools": self.requires_tools,
        }

    def to_agent_prompt(self) -> str:
        prompt = f"""**Task: {self.title}**

{self.description}

**Success Criteria:**
"""
        if self.binary_checks:
            prompt += "- Binary checks: " + ", ".join(self.binary_checks) + "\n"
        if self.constraints:
            for c in self.constraints:
                prompt += f"- Constraint: {c['type']} = {c['value']}\n"
        if self.evaluation_rubric:
            prompt += f"- Qualitative: {self.evaluation_rubric}\n"
        return prompt


@dataclass
class Artifact:
    """An output produced by a task."""

    id: str
    task_id: str
    artifact_type: ArtifactType
    file_path: Optional[str] = None
    content_hash: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "artifact_type": self.artifact_type.value,
            "file_path": self.file_path,
            "content_hash": self.content_hash,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass
class IterationResult:
    """Results from a single iteration of the outer loop."""

    iteration: int
    task_id: str
    outcome: str
    completion_confidence: CompletionConfidence
    context_tokens_used: int
    tools_invoked: List[str]
    learnings: List[str]
    artifacts_produced: List[str]
    failed_approaches: List[Dict[str, Any]]
    agent_output: str
    commit_sha: Optional[str] = None


class GitCheckpointer:
    """Deterministic checkpointing via Git commits."""

    def __init__(self, workspace_path: Path):
        self.workspace = workspace_path
        self._ensure_git_initialized()

    def _ensure_git_initialized(self) -> None:
        git_dir = self.workspace / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init"], cwd=self.workspace, capture_output=True, check=True)
            subprocess.run(
                ["git", "add", "-A"], cwd=self.workspace, capture_output=True, check=False
            )
            subprocess.run(
                ["git", "commit", "-m", "[URW] Initialize workspace", "--allow-empty"],
                cwd=self.workspace,
                capture_output=True,
                check=False,
            )

    def _run_git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            capture_output=True,
            text=True,
        )

    def checkpoint(
        self,
        message: str,
        iteration: int,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        self._run_git("add", "-A")
        full_message = (
            f"[URW] {message}\n\n"
            f"iteration: {iteration}\n"
            f"task_id: {task_id or 'N/A'}\n"
            f"timestamp: {datetime.utcnow().isoformat()}Z\n"
            f"metadata: {json.dumps(metadata or {})}\n"
        )
        self._run_git("commit", "-m", full_message, "--allow-empty")
        sha_result = self._run_git("rev-parse", "HEAD")
        return sha_result.stdout.strip()

    def rollback_to(self, commit_sha: str) -> bool:
        result = self._run_git("checkout", commit_sha, "--", ".urw/")
        return result.returncode == 0

    def get_checkpoint_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        result = self._run_git("log", f"--max-count={limit}", "--format=%H|%s|%ai", "--grep=[URW]")
        checkpoints = []
        for line in result.stdout.strip().split("\n"):
            if line and "|" in line:
                parts = line.split("|")
                if len(parts) >= 3:
                    checkpoints.append({"sha": parts[0], "message": parts[1], "timestamp": parts[2]})
        return checkpoints

    def diff_from_checkpoint(self, commit_sha: str) -> str:
        result = self._run_git("diff", commit_sha, "--", ".urw/")
        return result.stdout

    def create_branch(self, branch_name: str) -> bool:
        result = self._run_git("checkout", "-b", branch_name)
        return result.returncode == 0

    def merge_branch(self, branch_name: str) -> bool:
        result = self._run_git("merge", branch_name, "--no-ff")
        return result.returncode == 0

    def get_current_sha(self) -> str:
        result = self._run_git("rev-parse", "HEAD")
        return result.stdout.strip()


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'pending',
    parent_task_id TEXT,
    verification_type TEXT DEFAULT 'composite',
    binary_checks JSON,
    constraints JSON,
    evaluation_rubric TEXT,
    minimum_acceptable_score REAL DEFAULT 0.7,
    max_iterations INTEGER DEFAULT 10,
    requires_tools JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    iteration_started INTEGER,
    iteration_completed INTEGER,
    FOREIGN KEY (parent_task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL,
    depends_on_task_id TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on_task_id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (depends_on_task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    file_path TEXT,
    content_hash TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metadata JSON,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS side_effects (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    effect_type TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    details JSON,
    executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    iteration INTEGER,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS iterations (
    iteration INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    task_id TEXT,
    outcome TEXT,
    completion_confidence TEXT,
    context_tokens_used INTEGER,
    tools_invoked JSON,
    learnings JSON,
    artifacts_produced JSON,
    agent_output TEXT,
    commit_sha TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS failed_approaches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT,
    approach TEXT NOT NULL,
    why_failed TEXT NOT NULL,
    iteration INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS plan_metadata (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS verification_findings (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    iteration INTEGER,
    status TEXT,
    evidence_type TEXT,
    evidence_refs JSON,
    summary JSON,
    task_type TEXT,
    verifier_version TEXT,
    verification_timestamp TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts(task_id);
CREATE INDEX IF NOT EXISTS idx_side_effects_key ON side_effects(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_iterations_task ON iterations(task_id);
CREATE INDEX IF NOT EXISTS idx_failed_approaches_task ON failed_approaches(task_id);
"""


class URWStateManager:
    """Deterministic state management for Universal Ralph Wrapper."""

    def __init__(self, workspace_path: Path):
        self.workspace = Path(workspace_path)
        self.urw_dir = self.workspace / ".urw"
        self.db_path = self.urw_dir / "state.db"
        self.artifacts_dir = self.urw_dir / "artifacts"
        self.iterations_dir = self.urw_dir / "iterations"
        self.verification_dir = self.urw_dir / "verification"

        self._ensure_initialized()
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema_migrations()
        self.checkpointer = GitCheckpointer(self.workspace)

    def _ensure_initialized(self) -> None:
        if not self.urw_dir.exists():
            self.urw_dir.mkdir(parents=True)
            self.artifacts_dir.mkdir()
            self.iterations_dir.mkdir()
            self.verification_dir.mkdir()

            conn = sqlite3.connect(self.db_path)
            conn.executescript(SCHEMA)
            conn.close()

            (self.urw_dir / "progress.md").write_text("# Progress\n\nNo work started yet.\n")
            (self.urw_dir / "guardrails.md").write_text(
                "# Guardrails\n\nNo failed approaches recorded yet.\n"
            )
            (self.urw_dir / "task_plan.json").write_text("[]")
        else:
            self.verification_dir.mkdir(exist_ok=True)

    def _ensure_schema_migrations(self) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(verification_findings)").fetchall()
        }
        additions = {
            "task_type": "TEXT",
            "verifier_version": "TEXT",
            "verification_timestamp": "TEXT",
            "notes": "TEXT",
        }
        for name, col_type in additions.items():
            if name not in columns:
                self.conn.execute(
                    f"ALTER TABLE verification_findings ADD COLUMN {name} {col_type}"
                )
        self.conn.commit()

    @contextmanager
    def transaction(self):
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def set_plan_metadata(self, key: str, value: Any) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO plan_metadata (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (key, json.dumps(value)),
        )
        self.conn.commit()

    def get_plan_metadata(self, key: str) -> Optional[Any]:
        row = self.conn.execute("SELECT value FROM plan_metadata WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    def get_original_request(self) -> Optional[str]:
        return self.get_plan_metadata("original_request")

    def set_original_request(self, request: str) -> None:
        self.set_plan_metadata("original_request", request)

    def mark_active_tasks_failed(self, reason: str) -> int:
        row = self.conn.execute(
            """
            SELECT COUNT(*) as count FROM tasks
            WHERE status NOT IN ('complete', 'failed')
            """
        ).fetchone()
        count = row["count"] if row else 0
        if count == 0:
            return 0

        self.conn.execute(
            """
            UPDATE tasks
            SET status = ?
            WHERE status NOT IN ('complete', 'failed')
            """,
            (TaskStatus.FAILED.value,),
        )
        self.conn.commit()
        self.set_plan_metadata(
            "plan_superseded",
            {"reason": reason, "timestamp": datetime.utcnow().isoformat() + "Z"},
        )
        self._update_task_plan_file()
        self._update_progress_file()
        return count

    def create_task(self, task: Task) -> str:
        with self.transaction():
            self.conn.execute(
                """
                INSERT INTO tasks (
                    id, title, description, status, parent_task_id,
                    verification_type, binary_checks, constraints,
                    evaluation_rubric, minimum_acceptable_score,
                    max_iterations, requires_tools
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.title,
                    task.description,
                    task.status.value,
                    task.parent_task_id,
                    task.verification_type,
                    json.dumps(task.binary_checks),
                    json.dumps(task.constraints),
                    task.evaluation_rubric,
                    task.minimum_acceptable_score,
                    task.max_iterations,
                    json.dumps(task.requires_tools),
                ),
            )
            for dep_id in task.depends_on:
                self.conn.execute(
                    """
                    INSERT INTO task_dependencies (task_id, depends_on_task_id)
                    VALUES (?, ?)
                    """,
                    (task.id, dep_id),
                )
        self._update_task_plan_file()
        return task.id

    def create_tasks_batch(self, tasks: List[Task]) -> None:
        with self.transaction():
            for task in tasks:
                self.conn.execute(
                    """
                    INSERT INTO tasks (
                        id, title, description, status, parent_task_id,
                        verification_type, binary_checks, constraints,
                        evaluation_rubric, minimum_acceptable_score,
                        max_iterations, requires_tools
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.id,
                        task.title,
                        task.description,
                        task.status.value,
                        task.parent_task_id,
                        task.verification_type,
                        json.dumps(task.binary_checks),
                        json.dumps(task.constraints),
                        task.evaluation_rubric,
                        task.minimum_acceptable_score,
                        task.max_iterations,
                        json.dumps(task.requires_tools),
                    ),
                )
                for dep_id in task.depends_on:
                    self.conn.execute(
                        """
                        INSERT INTO task_dependencies (task_id, depends_on_task_id)
                        VALUES (?, ?)
                        """,
                        (task.id, dep_id),
                    )
        self._update_task_plan_file()

    def get_task(self, task_id: str) -> Optional[Task]:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def get_all_tasks(self) -> List[Task]:
        rows = self.conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_next_task(self) -> Optional[Task]:
        row = self.conn.execute(
            """
            SELECT t.* FROM tasks t
            WHERE t.status = 'pending'
            AND NOT EXISTS (
                SELECT 1 FROM task_dependencies td
                JOIN tasks dep ON td.depends_on_task_id = dep.id
                WHERE td.task_id = t.id
                AND dep.status != 'complete'
            )
            ORDER BY t.created_at
            LIMIT 1
            """
        ).fetchone()
        return self._row_to_task(row) if row else None

    def get_tasks_by_status(self, status: TaskStatus) -> List[Task]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at",
            (status.value,),
        ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def update_task_status(self, task_id: str, status: TaskStatus, iteration: Optional[int] = None) -> None:
        updates = ["status = ?"]
        params: List[Any] = [status.value]

        if status == TaskStatus.IN_PROGRESS and iteration:
            updates.append("started_at = CURRENT_TIMESTAMP")
            updates.append("iteration_started = ?")
            params.append(iteration)
        elif status == TaskStatus.COMPLETE and iteration:
            updates.append("completed_at = CURRENT_TIMESTAMP")
            updates.append("iteration_completed = ?")
            params.append(iteration)

        params.append(task_id)
        self.conn.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self.conn.commit()
        self._update_task_plan_file()
        self._update_progress_file()

    def get_task_iteration_count(self, task_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) as count FROM iterations WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return row["count"] if row else 0

    def is_plan_complete(self) -> bool:
        row = self.conn.execute(
            """
            SELECT COUNT(*) as count FROM tasks
            WHERE status NOT IN ('complete', 'failed')
            """
        ).fetchone()
        return row["count"] == 0

    def get_completion_stats(self) -> Dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) as count FROM tasks GROUP BY status"
        ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    def register_artifact(self, artifact: Artifact, content: Optional[bytes | str] = None) -> str:
        if content:
            if isinstance(content, str):
                content = content.encode("utf-8")
            artifact.content_hash = hashlib.sha256(content).hexdigest()[:16]
            if artifact.file_path:
                file_path = self.artifacts_dir / artifact.file_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(content)

        self.conn.execute(
            """
            INSERT INTO artifacts (id, task_id, artifact_type, file_path,
                                   content_hash, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.id,
                artifact.task_id,
                artifact.artifact_type.value,
                artifact.file_path,
                artifact.content_hash,
                json.dumps(artifact.metadata) if artifact.metadata else None,
            ),
        )
        self.conn.commit()
        return artifact.id

    def get_artifact(self, artifact_id: str) -> Optional[Artifact]:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        return self._row_to_artifact(row) if row else None

    def get_task_artifacts(self, task_id: str) -> List[Artifact]:
        rows = self.conn.execute(
            "SELECT * FROM artifacts WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        return [self._row_to_artifact(row) for row in rows]

    def get_artifact_content(self, artifact_id: str) -> Optional[bytes]:
        row = self.conn.execute(
            "SELECT file_path FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        if row and row["file_path"]:
            file_path = self.artifacts_dir / row["file_path"]
            if file_path.exists():
                return file_path.read_bytes()
        return None

    def get_artifact_path(self, artifact_id: str) -> Optional[Path]:
        row = self.conn.execute(
            "SELECT file_path FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
        if row and row["file_path"]:
            return self.artifacts_dir / row["file_path"]
        return None

    def record_side_effect(
        self,
        task_id: str,
        effect_type: str,
        idempotency_key: str,
        details: Dict[str, Any],
        iteration: int,
    ) -> bool:
        existing = self.conn.execute(
            "SELECT id FROM side_effects WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing:
            return False

        effect_id = f"effect_{idempotency_key[:8]}_{iteration}"
        self.conn.execute(
            """
            INSERT INTO side_effects (id, task_id, effect_type, idempotency_key,
                                      details, iteration)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                effect_id,
                task_id,
                effect_type,
                idempotency_key,
                json.dumps(details),
                iteration,
            ),
        )
        self.conn.commit()
        self._write_receipt_confirmation(
            effect_id=effect_id,
            task_id=task_id,
            effect_type=effect_type,
            idempotency_key=idempotency_key,
            details=details,
            iteration=iteration,
        )
        return True

    def _write_receipt_confirmation(
        self,
        effect_id: str,
        task_id: str,
        effect_type: str,
        idempotency_key: str,
        details: Dict[str, Any],
        iteration: int,
    ) -> None:
        provider_id = (
            details.get("message_id")
            or details.get("id")
            or details.get("messageId")
            or idempotency_key
        )
        record = {
            "task_id": task_id,
            "tool_name": details.get("tool"),
            "provider_id": provider_id,
            "effect_type": effect_type,
            "evidence_type": "receipt",
            "iteration": iteration,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        path = self.verification_dir / f"receipt_{effect_id}.json"
        path.write_text(json.dumps(record, indent=2))

    def was_side_effect_executed(self, idempotency_key: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM side_effects WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return row is not None

    def get_task_side_effects(self, task_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM side_effects WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def start_iteration(self, task_id: str) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO iterations (task_id, outcome)
            VALUES (?, 'in_progress')
            """,
            (task_id,),
        )
        self.conn.commit()
        iteration = cursor.lastrowid
        iteration_file = self.iterations_dir / f"{iteration:03d}_started.json"
        iteration_file.write_text(
            json.dumps(
                {
                    "iteration": iteration,
                    "task_id": task_id,
                    "started_at": datetime.utcnow().isoformat() + "Z",
                    "status": "started",
                },
                indent=2,
            )
        )
        return iteration

    def complete_iteration(self, result: IterationResult) -> str:
        self.conn.execute(
            """
            UPDATE iterations
            SET completed_at = CURRENT_TIMESTAMP,
                outcome = ?,
                completion_confidence = ?,
                context_tokens_used = ?,
                tools_invoked = ?,
                learnings = ?,
                artifacts_produced = ?,
                agent_output = ?
            WHERE iteration = ?
            """,
            (
                result.outcome,
                result.completion_confidence.value,
                result.context_tokens_used,
                json.dumps(result.tools_invoked),
                json.dumps(result.learnings),
                json.dumps(result.artifacts_produced),
                result.agent_output,
                result.iteration,
            ),
        )
        self.conn.commit()

        for failure in result.failed_approaches:
            self.record_failed_approach(
                failure["approach"],
                failure["why_failed"],
                task_id=result.task_id,
                iteration=result.iteration,
            )

        iteration_file = self.iterations_dir / f"{result.iteration:03d}_complete.json"
        iteration_file.write_text(
            json.dumps(
                {
                    "iteration": result.iteration,
                    "task_id": result.task_id,
                    "completed_at": datetime.utcnow().isoformat() + "Z",
                    "outcome": result.outcome,
                    "completion_confidence": result.completion_confidence.value,
                    "context_tokens_used": result.context_tokens_used,
                    "tools_invoked": result.tools_invoked,
                    "learnings": result.learnings,
                    "artifacts_produced": result.artifacts_produced,
                },
                indent=2,
            )
        )

        self._update_progress_file()
        self._update_guardrails_file()

        commit_sha = self.checkpointer.checkpoint(
            message=f"Iteration {result.iteration}: {result.outcome}",
            iteration=result.iteration,
            task_id=result.task_id,
            metadata={
                "learnings": result.learnings,
                "completion_confidence": result.completion_confidence.value,
            },
        )

        self.conn.execute(
            "UPDATE iterations SET commit_sha = ? WHERE iteration = ?",
            (commit_sha, result.iteration),
        )
        self.conn.commit()

        return commit_sha

    def get_iteration(self, iteration: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM iterations WHERE iteration = ?",
            (iteration,),
        ).fetchone()
        return dict(row) if row else None

    def get_recent_iterations(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT i.*, t.title as task_title
            FROM iterations i
            LEFT JOIN tasks t ON i.task_id = t.id
            ORDER BY i.iteration DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_failed_approach(
        self,
        approach: str,
        why_failed: str,
        task_id: Optional[str] = None,
        iteration: Optional[int] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO failed_approaches (task_id, approach, why_failed, iteration)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, approach, why_failed, iteration),
        )
        self.conn.commit()
        self._update_guardrails_file()

    def get_failed_approaches(self, task_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        if task_id:
            rows = self.conn.execute(
                """
                SELECT * FROM failed_approaches
                WHERE task_id = ? OR task_id IS NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (task_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM failed_approaches
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def generate_agent_context(self, task: Task, max_tokens: int = 4000) -> str:
        sections: List[str] = []

        stats = self.get_completion_stats()
        sections.append(
            """## Plan Status
- Complete: {complete} | In Progress: {in_progress} | Pending: {pending} | Blocked: {blocked}
""".format(
                complete=stats.get("complete", 0),
                in_progress=stats.get("in_progress", 0),
                pending=stats.get("pending", 0),
                blocked=stats.get("blocked", 0),
            )
        )

        sections.append(
            f"""## Your Current Task
**{task.title}**

{task.description}

**Success Criteria:**"""
        )

        if task.binary_checks:
            sections.append("- Binary checks: " + ", ".join(task.binary_checks))
        if task.constraints:
            for c in task.constraints:
                sections.append(f"- Constraint: {c['type']} = {c['value']}")
        if task.evaluation_rubric:
            sections.append(f"- Qualitative: {task.evaluation_rubric}")

        sections.append("")

        dep_artifacts: List[str] = []
        for dep_id in task.depends_on:
            dep_task = self.get_task(dep_id)
            artifacts = self.get_task_artifacts(dep_id)
            for art in artifacts:
                if art.file_path:
                    artifact_path = self.artifacts_dir / art.file_path
                    dep_artifacts.append(
                        f"- `{artifact_path}` ({art.artifact_type.value}) - from: {dep_task.title if dep_task else dep_id}"
                    )

        if dep_artifacts:
            sections.append(
                """## Available Inputs (from completed tasks)
{artifacts}
""".format(artifacts="\n".join(dep_artifacts))
            )

        learnings_rows = self.conn.execute(
            """
            SELECT learnings FROM iterations
            WHERE learnings IS NOT NULL
            ORDER BY iteration DESC LIMIT 5
            """
        ).fetchall()

        all_learnings: List[str] = []
        for row in learnings_rows:
            parsed = json.loads(row["learnings"])
            if parsed:
                all_learnings.extend(parsed)

        if all_learnings:
            sections.append(
                """## Key Learnings (Apply These)
{learnings}
""".format(learnings="\n".join(f"- {l}" for l in all_learnings[-7:]))
            )

        failed = self.get_failed_approaches(task.id, limit=10)
        if failed:
            sections.append(
                """## Failed Approaches (DO NOT REPEAT THESE)
{fails}
""".format(
                    fails="\n".join(
                        f"- **{f['approach']}**: {f['why_failed']}" for f in failed[:7]
                    )
                )
            )

        effects = self.get_task_side_effects(task.id)
        if effects:
            effect_summaries = []
            for e in effects:
                details = json.loads(e["details"]) if isinstance(e["details"], str) else e["details"]
                summary = details.get("summary", "done")
                effect_summaries.append(f"- {e['effect_type']}: {summary}")

            sections.append(
                """## Actions Already Taken (Don't Repeat)
{effects}
""".format(effects="\n".join(effect_summaries))
            )

        iterations_used = self.get_task_iteration_count(task.id)
        sections.append(
            """## Iteration Budget
- Used: {used} / {total} iterations for this task
""".format(used=iterations_used, total=task.max_iterations)
        )

        return "\n".join(sections)

    def write_verification_finding(
        self,
        task_id: str,
        iteration: int,
        status: str,
        evidence_type: str,
        evidence_refs: List[str],
        summary: Dict[str, Any],
        task_type: Optional[str] = None,
        verifier_version: str = "urw_v1",
        notes: Optional[str] = None,
    ) -> Path:
        finding_id = f"verify_{task_id}_{iteration}"
        timestamp = datetime.utcnow().isoformat() + "Z"
        record = {
            "verification_id": finding_id,
            "task_id": task_id,
            "task_type": task_type,
            "iteration": iteration,
            "status": status,
            "evidence_type": evidence_type,
            "evidence_refs": evidence_refs,
            "verifier_version": verifier_version,
            "verification_timestamp": timestamp,
            "notes": notes,
            "summary": summary,
            "timestamp": timestamp,
        }
        path = self.verification_dir / f"{finding_id}.json"
        path.write_text(json.dumps(record, indent=2))

        self.conn.execute(
            """
            INSERT OR REPLACE INTO verification_findings
            (id, task_id, iteration, status, evidence_type, evidence_refs, summary,
             task_type, verifier_version, verification_timestamp, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding_id,
                task_id,
                iteration,
                status,
                evidence_type,
                json.dumps(evidence_refs),
                json.dumps(summary),
                task_type,
                verifier_version,
                timestamp,
                notes,
            ),
        )
        self.conn.commit()

        return path

    def _update_task_plan_file(self) -> None:
        rows = self.conn.execute(
            """
            SELECT id, title, status, parent_task_id FROM tasks
            ORDER BY created_at
            """
        ).fetchall()
        plan = [dict(row) for row in rows]
        (self.urw_dir / "task_plan.json").write_text(json.dumps(plan, indent=2))

    def _update_progress_file(self) -> None:
        stats = self.get_completion_stats()
        total = sum(stats.values())
        complete = stats.get("complete", 0)

        recent = self.get_recent_iterations(10)

        learnings_rows = self.conn.execute(
            """
            SELECT learnings FROM iterations
            WHERE learnings IS NOT NULL
            ORDER BY iteration DESC LIMIT 5
            """
        ).fetchall()

        all_learnings: List[str] = []
        for row in learnings_rows:
            parsed = json.loads(row["learnings"])
            if parsed:
                all_learnings.extend(parsed)

        current_task = self.get_tasks_by_status(TaskStatus.IN_PROGRESS)
        next_task = self.get_next_task()

        content = f"""# Progress

## Status Summary
- ✅ Complete: {stats.get('complete', 0)}
- 🔄 In Progress: {stats.get('in_progress', 0)}
- ⏳ Pending: {stats.get('pending', 0)}
- 🚫 Blocked: {stats.get('blocked', 0)}
- ❌ Failed: {stats.get('failed', 0)}

**Overall: {complete}/{total} tasks complete ({100*complete//total if total > 0 else 0}%)**

## Current Task
{current_task[0].title if current_task else 'None'}

## Next Task
{next_task.title if next_task else 'None (plan complete or blocked)'}

## Recent Iterations
{chr(10).join(f'- [{r["iteration"]}] {r.get("task_title", "N/A")}: {r["outcome"]} ({r.get("completion_confidence", "N/A")})' for r in recent[:10])}

## Key Learnings
{chr(10).join(f'- {l}' for l in all_learnings[-10:])}

---
*Last updated: {datetime.utcnow().isoformat()}Z*
"""
        (self.urw_dir / "progress.md").write_text(content)

    def _update_guardrails_file(self) -> None:
        failed = self.get_failed_approaches(limit=50)

        if not failed:
            content = (
                "# Guardrails\n\n"
                "No failed approaches recorded yet. Good luck!\n\n"
                f"---\n*Last updated: {datetime.utcnow().isoformat()}Z*\n"
            )
        else:
            entries = []
            for f in failed:
                entries.append(
                    """### ❌ {approach}
- **Why it failed:** {why_failed}
- **Task:** {task}
- **Iteration:** {iteration}
""".format(
                        approach=f["approach"],
                        why_failed=f["why_failed"],
                        task=f["task_id"] or "General",
                        iteration=f["iteration"] or "N/A",
                    )
                )

            content = (
                "# Guardrails\n\n"
                "**READ THIS FIRST.** These approaches have been tried and failed. Do not repeat them.\n\n"
                f"{chr(10).join(entries)}\n\n"
                f"---\n*Last updated: {datetime.utcnow().isoformat()}Z*\n"
            )
        (self.urw_dir / "guardrails.md").write_text(content)

    def _row_to_task(self, row) -> Task:
        deps = self.conn.execute(
            "SELECT depends_on_task_id FROM task_dependencies WHERE task_id = ?",
            (row["id"],),
        ).fetchall()
        return Task(
            id=row["id"],
            title=row["title"],
            description=row["description"] or "",
            status=TaskStatus(row["status"]),
            parent_task_id=row["parent_task_id"],
            depends_on=[d["depends_on_task_id"] for d in deps],
            verification_type=row["verification_type"] or "composite",
            binary_checks=json.loads(row["binary_checks"] or "[]"),
            constraints=json.loads(row["constraints"] or "[]"),
            evaluation_rubric=row["evaluation_rubric"],
            minimum_acceptable_score=row["minimum_acceptable_score"] or 0.7,
            max_iterations=row["max_iterations"] or 10,
            requires_tools=json.loads(row["requires_tools"] or "[]"),
            iteration_started=row["iteration_started"],
            iteration_completed=row["iteration_completed"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )

    def _row_to_artifact(self, row) -> Artifact:
        return Artifact(
            id=row["id"],
            task_id=row["task_id"],
            artifact_type=ArtifactType(row["artifact_type"]),
            file_path=row["file_path"],
            content_hash=row["content_hash"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            created_at=row["created_at"],
        )


def estimate_task_complexity(request: str) -> int:
    length_score = min(len(request) // 200, 10)
    keyword_score = sum(1 for k in ["research", "report", "analysis", "multi-step"] if k in request.lower())
    return length_score + keyword_score


def validate_task_graph(tasks: List[Task]) -> bool:
    task_ids = {t.id for t in tasks}
    for task in tasks:
        for dep in task.depends_on:
            if dep not in task_ids:
                return False
    return True
