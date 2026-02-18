import pytest

from universal_agent.bot.task_manager import TaskManager


@pytest.mark.asyncio
async def test_add_task_rejects_second_active_task_for_same_user():
    manager = TaskManager()

    first_id = await manager.add_task(user_id=123, prompt="first")

    with pytest.raises(ValueError) as exc:
        await manager.add_task(user_id=123, prompt="second")

    assert str(exc.value) == f"active_task:{first_id}"


@pytest.mark.asyncio
async def test_continuation_mode_sets_task_continue_session_flag():
    manager = TaskManager()
    manager.enable_continuation(456)

    task_id = await manager.add_task(user_id=456, prompt="continue me")
    task = manager.get_task(task_id)
    assert task is not None
    assert task.continue_session is True

    # Mark complete so another task can be queued for the same user.
    task.status = "completed"
    manager.disable_continuation(456)

    task_id_2 = await manager.add_task(user_id=456, prompt="fresh now")
    task_2 = manager.get_task(task_id_2)
    assert task_2 is not None
    assert task_2.continue_session is False


def test_cancel_pending_task_succeeds():
    manager = TaskManager()
    # direct task injection for deterministic cancel semantics
    from universal_agent.bot.task_manager import Task

    t = Task(user_id=777, prompt="pending")
    manager.tasks[t.id] = t

    ok, detail = manager.cancel_task(777, t.id)
    assert ok is True
    assert detail == t.id
    assert manager.tasks[t.id].status == "canceled"


def test_cancel_running_task_rejected():
    from universal_agent.bot.task_manager import Task

    manager = TaskManager()
    t = Task(user_id=888, prompt="running")
    t.status = "running"
    manager.tasks[t.id] = t

    ok, detail = manager.cancel_task(888, t.id)
    assert ok is False
    assert "cannot be canceled" in detail


def test_cancel_task_wrong_owner_rejected():
    from universal_agent.bot.task_manager import Task

    manager = TaskManager()
    t = Task(user_id=999, prompt="owner only")
    manager.tasks[t.id] = t

    ok, detail = manager.cancel_task(111, t.id)
    assert ok is False
    assert detail == "Task does not belong to you."
