"""Reminder management action.

Reminders are stored in UTC. The scheduler in app/core/reminder_scheduler.py
fires them when they come due.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import BaseAction
from app.db.models import Reminder
from app.utils import from_db_datetime, parse_user_datetime


def reminder_to_dict(r: Reminder) -> dict[str, Any]:
    remind_at = from_db_datetime(r.remind_at)
    return {
        "id": r.id,
        "message": r.message,
        "remind_at": remind_at.isoformat() if remind_at else None,
        "triggered": r.triggered,
    }


class ReminderAction(BaseAction):
    """Set and manage reminders."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def name(self) -> str:
        return "reminders"

    @property
    def description(self) -> str:
        return "Set and manage reminders"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action")
        if not action:
            return {"success": False, "message": "Missing 'action' parameter."}

        handlers = {
            "create_reminder": self._create_reminder,
            "list_reminders": self._list_reminders,
            "dismiss_reminder": self._dismiss_reminder,
        }
        handler = handlers.get(action)
        if not handler:
            return {"success": False, "message": f"Unknown action: {action}"}
        try:
            return await handler(params)
        except Exception as e:
            await self.session.rollback()
            return {"success": False, "message": f"Error executing reminder action: {e}"}

    async def _create_reminder(self, params: dict[str, Any]) -> dict[str, Any]:
        message = (params.get("message") or "").strip()
        remind_at = params.get("remind_at")
        if not message or not remind_at:
            return {"success": False, "message": "Both 'message' and 'remind_at' are required."}

        try:
            remind_at = parse_user_datetime(remind_at)
        except ValueError:
            return {"success": False, "message": "Invalid remind_at format. Use ISO format, e.g. 2026-12-25T10:00:00."}

        reminder = Reminder(message=message, remind_at=remind_at)
        self.session.add(reminder)
        await self.session.commit()
        await self.session.refresh(reminder)

        return {
            "success": True,
            "message": f"Reminder #{reminder.id} set: {message}",
            "reminder_id": reminder.id,
            "reminder": reminder_to_dict(reminder),
        }

    async def _list_reminders(self, params: dict[str, Any]) -> dict[str, Any]:
        stmt = select(Reminder).order_by(Reminder.remind_at.asc())
        triggered = params.get("triggered")
        if triggered is not None:
            stmt = stmt.where(Reminder.triggered == bool(triggered))

        reminders = (await self.session.execute(stmt)).scalars().all()
        return {
            "success": True,
            "message": f"Found {len(reminders)} reminders.",
            "reminders": [reminder_to_dict(r) for r in reminders],
        }

    async def _dismiss_reminder(self, params: dict[str, Any]) -> dict[str, Any]:
        reminder_id = params.get("reminder_id")
        if not reminder_id:
            return {"success": False, "message": "reminder_id is required."}

        reminder = await self.session.get(Reminder, int(reminder_id))
        if not reminder:
            return {"success": False, "message": f"Reminder {reminder_id} not found."}

        reminder.triggered = True
        await self.session.commit()
        return {"success": True, "message": f"Reminder #{reminder_id} dismissed."}
