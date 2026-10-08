"""Task management action."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import BaseAction
from app.db.models import Task
from app.utils import from_db_datetime, parse_user_datetime, utc_now

PRIORITIES = ("low", "medium", "high")
_PRIORITY_ALIASES = {
    "1": "high", "2": "medium", "3": "low",
    "urgent": "high", "important": "high", "normal": "medium",
}


def normalize_priority(value: Any) -> str:
    """Map whatever the user/LLM gave (1, 'High', 'urgent', None) to low/medium/high."""
    if value is None or value == "":
        return "medium"
    text = str(value).strip().lower()
    if text in PRIORITIES:
        return text
    return _PRIORITY_ALIASES.get(text, "medium")


def task_to_dict(t: Task) -> dict[str, Any]:
    due = from_db_datetime(t.due_date)
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "completed": t.completed,
        "priority": t.priority,
        "due_date": due.isoformat() if due else None,
    }


class TaskAction(BaseAction):
    """Manage personal tasks - create, list, complete, reopen and delete."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def name(self) -> str:
        return "tasks"

    @property
    def description(self) -> str:
        return "Manage personal tasks - create, list, complete, and delete tasks"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action")
        if not action:
            return {"success": False, "message": "Missing 'action' parameter."}

        handlers = {
            "create_task": self._create_task,
            "list_tasks": self._list_tasks,
            "complete_task": self._complete_task,
            "reopen_task": self._reopen_task,
            "delete_task": self._delete_task,
        }
        handler = handlers.get(action)
        if not handler:
            return {"success": False, "message": f"Unknown action: {action}"}
        try:
            return await handler(params)
        except Exception as e:
            await self.session.rollback()
            return {"success": False, "message": f"Error executing task action: {e}"}

    async def _create_task(self, params: dict[str, Any]) -> dict[str, Any]:
        title = (params.get("title") or "").strip()
        if not title:
            return {"success": False, "message": "Title is required to create a task."}

        due_date = params.get("due_date")
        if due_date:
            try:
                due_date = parse_user_datetime(due_date)
            except ValueError:
                return {"success": False, "message": "Invalid due_date format. Use ISO format."}
        else:
            due_date = None

        task = Task(
            title=title,
            description=params.get("description") or "",
            priority=normalize_priority(params.get("priority")),
            due_date=due_date,
        )
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)

        return {
            "success": True,
            "message": f"Created task #{task.id}: {task.title} ({task.priority} priority)",
            "task_id": task.id,
            "task": task_to_dict(task),
        }

    async def _list_tasks(self, params: dict[str, Any]) -> dict[str, Any]:
        stmt = select(Task).order_by(Task.completed, Task.created_at.desc())
        completed = params.get("completed")
        if completed is not None:
            stmt = stmt.where(Task.completed == bool(completed))

        tasks = (await self.session.execute(stmt)).scalars().all()
        return {
            "success": True,
            "message": f"Found {len(tasks)} tasks.",
            "tasks": [task_to_dict(t) for t in tasks],
        }

    async def _get(self, params: dict[str, Any]) -> Task | None:
        task_id = params.get("task_id")
        if not task_id:
            return None
        return await self.session.get(Task, int(task_id))

    async def _complete_task(self, params: dict[str, Any]) -> dict[str, Any]:
        if not params.get("task_id"):
            return {"success": False, "message": "task_id is required."}
        task = await self._get(params)
        if not task:
            return {"success": False, "message": f"Task {params['task_id']} not found."}

        task.completed = True
        task.completed_at = utc_now().replace(tzinfo=None)
        await self.session.commit()
        return {"success": True, "message": f"Task #{task.id} '{task.title}' marked as completed."}

    async def _reopen_task(self, params: dict[str, Any]) -> dict[str, Any]:
        if not params.get("task_id"):
            return {"success": False, "message": "task_id is required."}
        task = await self._get(params)
        if not task:
            return {"success": False, "message": f"Task {params['task_id']} not found."}

        task.completed = False
        task.completed_at = None
        await self.session.commit()
        return {"success": True, "message": f"Task #{task.id} '{task.title}' reopened."}

    async def _delete_task(self, params: dict[str, Any]) -> dict[str, Any]:
        if not params.get("task_id"):
            return {"success": False, "message": "task_id is required."}
        task = await self._get(params)
        if not task:
            return {"success": False, "message": f"Task {params['task_id']} not found."}

        title = task.title
        await self.session.delete(task)
        await self.session.commit()
        return {"success": True, "message": f"Task #{params['task_id']} '{title}' deleted."}
