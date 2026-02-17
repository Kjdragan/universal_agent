from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class FakeLabel:
    id: str
    name: str


@dataclass
class FakeProject:
    id: str
    name: str


@dataclass
class FakeSection:
    id: str
    name: str
    project_id: str


@dataclass
class FakeDue:
    date: str | None = None
    datetime: str | None = None
    is_recurring: bool = False


@dataclass
class FakeTask:
    id: str
    content: str
    description: str = ""
    priority: int = 1
    project_id: str = ""
    section_id: str | None = None
    parent_id: str | None = None
    labels: list[str] = field(default_factory=list)
    due: FakeDue | None = None
    url: str = ""
    created_at: str = ""
    comment_count: int = 0


class FakeTodoistAPI:
    def __init__(self):
        self.projects: list[FakeProject] = []
        self.sections: list[FakeSection] = []
        self.labels: list[FakeLabel] = []
        self.tasks: dict[str, FakeTask] = {}
        self.comments: dict[str, list[dict]] = {}

    def get_projects(self):
        return list(self.projects)

    def add_project(self, name: str):
        proj = FakeProject(id=f"proj_{len(self.projects)+1}", name=name)
        self.projects.append(proj)
        return proj

    def get_sections(self, project_id: str):
        return [s for s in self.sections if s.project_id == project_id]

    def add_section(self, name: str, project_id: str):
        sec = FakeSection(id=f"sec_{len(self.sections)+1}", name=name, project_id=project_id)
        self.sections.append(sec)
        return sec

    def get_labels(self):
        return list(self.labels)

    def add_label(self, name: str):
        lbl = FakeLabel(id=f"lbl_{len(self.labels)+1}", name=name)
        self.labels.append(lbl)
        return lbl

    def get_tasks(self, **kwargs):
        # Ignore filter parsing; we just return everything for unit tests.
        return list(self.tasks.values())

    def add_task(self, **kwargs):
        task_id = f"task_{len(self.tasks)+1}"
        task = FakeTask(
            id=task_id,
            content=kwargs.get("content") or "",
            description=kwargs.get("description") or "",
            priority=int(kwargs.get("priority") or 1),
            project_id=str(kwargs.get("project_id") or ""),
            section_id=kwargs.get("section_id"),
            parent_id=kwargs.get("parent_id"),
            labels=list(kwargs.get("labels") or []),
        )
        self.tasks[task_id] = task
        return task

    def get_task(self, task_id: str):
        return self.tasks[task_id]

    def update_task(self, task_id: str, **kwargs):
        task = self.tasks[task_id]
        for key, value in kwargs.items():
            setattr(task, key, value)

    def close_task(self, task_id: str):
        self.tasks.pop(task_id, None)

    def delete_task(self, task_id: str):
        self.tasks.pop(task_id, None)

    def get_comments(self, task_id: str):
        # Return iterator pages like the SDK.
        return [
            [
                type(
                    "FakeComment",
                    (),
                    {
                        "id": c.get("id"),
                        "content": c.get("content"),
                        "posted_at": c.get("posted_at"),
                    },
                )
                for c in self.comments.get(task_id, [])
            ]
        ]

    def add_comment(self, task_id: str, content: str):
        self.comments.setdefault(task_id, []).append(
            {"id": f"c_{len(self.comments.get(task_id, []))+1}", "content": content, "posted_at": "now"}
        )


def test_ensure_taxonomy_idempotent_creates_projects_sections_and_labels():
    from universal_agent.services.todoist_service import (
        AGENT_TASKS_PROJECT,
        AGENT_SECTIONS,
        BRAINSTORM_PROJECT,
        BRAINSTORM_SECTIONS,
        TodoService,
    )

    api = FakeTodoistAPI()
    svc = TodoService(api_token="test", api=api)

    res1 = svc.ensure_taxonomy()
    assert res1["agent_project_id"]
    assert res1["brainstorm_project_id"]
    assert set(res1["agent_sections"].keys()) == set(AGENT_SECTIONS.keys())
    assert set(res1["brainstorm_sections"].keys()) == set(BRAINSTORM_SECTIONS.keys())

    # Second call should not create duplicates.
    before_projects = len(api.projects)
    before_sections = len(api.sections)
    before_labels = len(api.labels)
    res2 = svc.ensure_taxonomy()
    assert res2["agent_project_id"] == res1["agent_project_id"]
    assert len(api.projects) == before_projects
    assert len(api.sections) == before_sections
    assert len(api.labels) == before_labels

    assert any(p.name == AGENT_TASKS_PROJECT for p in api.projects)
    assert any(p.name == BRAINSTORM_PROJECT for p in api.projects)


def test_create_task_applies_priority_mapping_and_agent_ready_label():
    from universal_agent.services.todoist_service import TodoService

    api = FakeTodoistAPI()
    svc = TodoService(api_token="test", api=api)
    svc.ensure_taxonomy()

    created = svc.create_task(
        content="Do thing",
        priority="urgent",
        section="background",
        labels=["foo"],
        sub_agent="research",
    )

    assert created["priority"] == "P1-Urgent"
    assert "agent-ready" in created["labels"]
    assert "foo" in created["labels"]
    assert "sub-agent:research" in created["labels"]


def test_mark_blocked_swaps_labels_and_adds_comment():
    from universal_agent.services.todoist_service import TodoService

    api = FakeTodoistAPI()
    svc = TodoService(api_token="test", api=api)
    svc.ensure_taxonomy()

    task = api.add_task(content="x", project_id="p", labels=["agent-ready"])
    ok = svc.mark_blocked(task.id, "waiting")
    assert ok is True

    updated = api.get_task(task.id)
    assert "agent-ready" not in updated.labels
    assert "blocked" in updated.labels
    assert api.comments[task.id]


def test_get_task_detail_includes_comments():
    from universal_agent.services.todoist_service import TodoService

    api = FakeTodoistAPI()
    svc = TodoService(api_token="test", api=api)
    task = api.add_task(content="x", project_id="p")
    api.add_comment(task.id, "hi")

    detail = svc.get_task_detail(task.id)
    assert detail is not None
    assert "comments" in detail
    assert len(detail["comments"]) == 1


def test_missing_token_raises_value_error():
    from universal_agent.services.todoist_service import TodoService

    with pytest.raises(ValueError):
        TodoService(api_token="")


def test_record_idea_dedupe_reuses_task_and_bumps_confidence():
    from universal_agent.services.todoist_service import TodoService

    api = FakeTodoistAPI()
    svc = TodoService(api_token="test", api=api)
    svc.ensure_taxonomy()

    first = svc.record_idea(content="Idea A", description="first", dedupe_key="idea-a")
    second = svc.record_idea(content="Idea A", description="second", dedupe_key="idea-a")

    assert first["id"] == second["id"]
    assert len(api.tasks) == 1
    task = api.get_task(first["id"])
    assert "confidence: 2" in task.description
    assert api.comments[first["id"]]


def test_pipeline_promote_park_and_summary_counts():
    from universal_agent.services.todoist_service import TodoService

    api = FakeTodoistAPI()
    svc = TodoService(api_token="test", api=api)
    taxonomy = svc.ensure_taxonomy()

    idea = svc.record_idea(content="Idea B", description="x")
    counts = svc.get_pipeline_summary()
    assert counts["inbox"] == 1

    ok_promote = svc.promote_idea(idea["id"], target_section="approved")
    assert ok_promote is True
    assert api.get_task(idea["id"]).section_id == taxonomy["brainstorm_sections"]["approved"]

    ok_park = svc.park_idea(idea["id"], rationale="not now")
    assert ok_park is True
    assert api.get_task(idea["id"]).section_id == taxonomy["brainstorm_sections"]["parked"]
    assert api.comments[idea["id"]]

    counts2 = svc.get_pipeline_summary()
    assert counts2["parked"] == 1
