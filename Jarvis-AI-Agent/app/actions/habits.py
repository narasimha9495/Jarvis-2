"""Habit tracking action module.

Track daily habits, build streaks, and view completion reports.
"""

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import BaseAction
from app.db.models import Habit, HabitLog
from app.utils import local_today

logger = logging.getLogger(__name__)


def streak_from_dates(logged: set[date], today: date) -> int:
    """Consecutive logged days ending today — or ending yesterday if today
    isn't logged yet, so a streak doesn't show 0 every morning."""
    day = today if today in logged else today - timedelta(days=1)
    streak = 0
    while day in logged:
        streak += 1
        day -= timedelta(days=1)
    return streak


class HabitAction(BaseAction):
    """Track daily habits and build streaks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def name(self) -> str:
        return "habits"

    @property
    def description(self) -> str:
        return "Track daily habits and build streaks"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action", "")
        handlers = {
            "create_habit": self._create_habit,
            "log_habit": self._log_habit,
            "list_habits": self._list_habits,
            "habit_report": self._habit_report,
            "delete_habit": self._delete_habit,
        }
        handler = handlers.get(action)
        if not handler:
            return {"success": False, "message": f"Unknown habit action: {action}"}
        return await handler(params)

    async def _find_habit(self, params: dict) -> Habit | None:
        """Look a habit up by id, or by name (case-insensitive) when the
        user says "log my exercise" and the LLM doesn't know the id."""
        if params.get("habit_id"):
            return await self._session.get(Habit, int(params["habit_id"]))
        name = (params.get("name") or "").strip()
        if not name:
            return None
        result = await self._session.execute(
            select(Habit).where(func.lower(Habit.name) == name.lower(), Habit.active == True)  # noqa: E712
        )
        return result.scalars().first()

    async def _create_habit(self, params: dict) -> dict[str, Any]:
        try:
            name = (params.get("name") or "").strip()
            if not name:
                return {"success": False, "message": "Habit name is required"}

            habit = Habit(name=name, frequency=params.get("frequency") or "daily")
            self._session.add(habit)
            await self._session.commit()
            await self._session.refresh(habit)

            return {"success": True, "message": f"Created habit #{habit.id}: {name}", "habit_id": habit.id}
        except Exception as e:
            await self._session.rollback()
            logger.error(f"Failed to create habit: {e}")
            return {"success": False, "message": f"Failed to create habit: {e}"}

    async def _log_habit(self, params: dict) -> dict[str, Any]:
        try:
            habit = await self._find_habit(params)
            if not habit:
                ref = params.get("habit_id") or params.get("name") or "?"
                return {"success": False, "message": f"Habit '{ref}' not found"}

            today = local_today()
            already = await self._session.execute(
                select(HabitLog.id).where(HabitLog.habit_id == habit.id, HabitLog.completed_date == today)
            )
            if already.first():
                streak = await self._calculate_streak(habit.id)
                return {
                    "success": True,
                    "message": f"Already logged '{habit.name}' today! Streak: {streak} day(s)",
                    "current_streak": streak,
                }

            self._session.add(HabitLog(habit_id=habit.id, completed_date=today))
            await self._session.commit()

            streak = await self._calculate_streak(habit.id)
            return {
                "success": True,
                "message": f"Logged '{habit.name}' ✅ Streak: {streak} day(s)",
                "current_streak": streak,
            }
        except Exception as e:
            await self._session.rollback()
            logger.error(f"Failed to log habit: {e}")
            return {"success": False, "message": f"Failed to log habit: {e}"}

    async def _list_habits(self, params: dict) -> dict[str, Any]:
        try:
            habits = (
                await self._session.execute(
                    select(Habit).where(Habit.active == True).order_by(Habit.created_at)  # noqa: E712
                )
            ).scalars().all()

            today = local_today()
            habit_list = []
            for habit in habits:
                dates = await self._logged_dates(habit.id)
                habit_list.append({
                    "id": habit.id,
                    "name": habit.name,
                    "frequency": habit.frequency,
                    "current_streak": streak_from_dates(dates, today),
                    "total_completions": len(dates),
                    "done_today": today in dates,
                })

            return {"success": True, "habits": habit_list, "message": f"Tracking {len(habit_list)} habit(s)"}
        except Exception as e:
            logger.error(f"Failed to list habits: {e}")
            return {"success": False, "message": f"Failed to list habits: {e}"}

    async def _habit_report(self, params: dict) -> dict[str, Any]:
        try:
            period = params.get("period") or "week"
            days = 7 if period == "week" else 30
            today = local_today()
            start_date = today - timedelta(days=days - 1)  # window includes today

            habits = (
                await self._session.execute(select(Habit).where(Habit.active == True))  # noqa: E712
            ).scalars().all()

            report = []
            for habit in habits:
                dates = await self._logged_dates(habit.id)
                completed_days = sum(1 for d in dates if start_date <= d <= today)
                report.append({
                    "name": habit.name,
                    "completed_days": completed_days,
                    "total_days": days,
                    "completion_rate": round(completed_days / days * 100, 1),
                    "streak": streak_from_dates(dates, today),
                })

            return {
                "success": True,
                "period": period,
                "habits": report,
                "message": f"Habit report for the last {days} days",
            }
        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            return {"success": False, "message": f"Failed to generate report: {e}"}

    async def _delete_habit(self, params: dict) -> dict[str, Any]:
        try:
            habit = await self._find_habit(params)
            if not habit:
                return {"success": False, "message": f"Habit {params.get('habit_id') or params.get('name')} not found"}

            name = habit.name
            await self._session.execute(delete(HabitLog).where(HabitLog.habit_id == habit.id))
            await self._session.delete(habit)
            await self._session.commit()
            return {"success": True, "message": f"Deleted habit: {name}"}
        except Exception as e:
            await self._session.rollback()
            logger.error(f"Failed to delete habit: {e}")
            return {"success": False, "message": f"Failed to delete habit: {e}"}

    async def _logged_dates(self, habit_id: int) -> set[date]:
        result = await self._session.execute(
            select(HabitLog.completed_date).where(HabitLog.habit_id == habit_id)
        )
        return set(result.scalars().all())

    async def _calculate_streak(self, habit_id: int) -> int:
        return streak_from_dates(await self._logged_dates(habit_id), local_today())
