"""Daily and weekly productivity summaries.

"Today" means the local calendar day of the machine running Jarvis;
timestamps in the database are UTC, so day boundaries are converted.
"""

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import BaseAction
from app.db.models import Expense, HabitLog, Note, Reminder, Task
from app.utils import local_day_bounds_utc, local_today, money

logger = logging.getLogger(__name__)


class DailySummaryAction(BaseAction):
    """Generate productivity summaries and insights."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def name(self) -> str:
        return "daily_summary"

    @property
    def description(self) -> str:
        return "Generate daily productivity summary and weekly trends"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "generate_summary": self._generate_summary,
            "weekly_report": self._weekly_report,
        }
        handler = handlers.get(params.get("action", ""))
        if not handler:
            return {"success": False, "message": f"Unknown summary action: {params.get('action')}"}
        return await handler(params)

    async def _generate_summary(self, params: dict) -> dict[str, Any]:
        try:
            today = local_today()
            start, end = local_day_bounds_utc(today)

            tasks_created = await self._count(
                select(func.count(Task.id)).where(Task.created_at.between(start, end))
            )
            # Tasks *completed* today, whenever they were created
            tasks_completed = await self._count(
                select(func.count(Task.id)).where(
                    Task.completed == True,  # noqa: E712
                    Task.completed_at.between(start, end),
                )
            )
            reminders_set = await self._count(
                select(func.count(Reminder.id)).where(Reminder.created_at.between(start, end))
            )
            notes_created = await self._count(
                select(func.count(Note.id)).where(Note.created_at.between(start, end))
            )
            expenses_total = round(
                (await self._session.execute(
                    select(func.sum(Expense.amount)).where(Expense.date == today)
                )).scalar() or 0,
                2,
            )
            habits_logged = await self._count(
                select(func.count(HabitLog.id)).where(HabitLog.completed_date == today)
            )
            overdue = await self._count(
                select(func.count(Task.id)).where(
                    Task.completed == False,  # noqa: E712
                    Task.due_date.is_not(None),
                    Task.due_date < start,
                )
            )

            score = self._calculate_score(tasks_completed, tasks_created, habits_logged, overdue)
            summary = {
                "tasks_created": tasks_created,
                "tasks_completed": tasks_completed,
                "reminders_set": reminders_set,
                "notes_created": notes_created,
                "expenses_total": expenses_total,
                "habits_logged": habits_logged,
                "overdue_tasks": overdue,
            }
            return {
                "success": True,
                "date": str(today),
                "summary": summary,
                "productivity_score": score,
                "message": self._score_message(score),
            }
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            return {"success": False, "message": f"Failed to generate summary: {e}"}

    async def _weekly_report(self, params: dict) -> dict[str, Any]:
        try:
            today = local_today()
            week_start = today - timedelta(days=6)        # this week = last 7 days incl. today
            prev_week_start = week_start - timedelta(days=7)

            this_start, _ = local_day_bounds_utc(week_start)
            prev_start, _ = local_day_bounds_utc(prev_week_start)
            _, today_end = local_day_bounds_utc(today)

            tasks_this_week = await self._count(
                select(func.count(Task.id)).where(Task.completed_at.between(this_start, today_end))
            )
            tasks_last_week = await self._count(
                select(func.count(Task.id)).where(
                    Task.completed_at >= prev_start, Task.completed_at < this_start
                )
            )
            habits_this_week = await self._count(
                select(func.count(HabitLog.id)).where(HabitLog.completed_date >= week_start)
            )
            habits_last_week = await self._count(
                select(func.count(HabitLog.id)).where(
                    HabitLog.completed_date >= prev_week_start,
                    HabitLog.completed_date < week_start,
                )
            )
            expenses_total = round(
                (await self._session.execute(
                    select(func.sum(Expense.amount)).where(Expense.date >= week_start)
                )).scalar() or 0,
                2,
            )

            task_trend = self._trend(tasks_this_week, tasks_last_week)
            habit_trend = self._trend(habits_this_week, habits_last_week)

            return {
                "success": True,
                "period": "week",
                "start_date": str(week_start),
                "end_date": str(today),
                "stats": {
                    "tasks_completed": tasks_this_week,
                    "habits_logged": habits_this_week,
                    "expenses_total": expenses_total,
                },
                "trends": {"tasks": task_trend, "habits": habit_trend},
                "message": (
                    f"This week: {tasks_this_week} tasks completed ({task_trend}), "
                    f"{habits_this_week} habits logged ({habit_trend}), "
                    f"{money(expenses_total)} spent"
                ),
            }
        except Exception as e:
            logger.error(f"Failed to generate weekly report: {e}")
            return {"success": False, "message": f"Failed to generate report: {e}"}

    async def _count(self, query) -> int:
        return (await self._session.execute(query)).scalar() or 0

    @staticmethod
    def _calculate_score(completed: int, created: int, habits: int, overdue: int) -> int:
        """Productivity score 0-100: base 50, +10 per completed task (max +30),
        +10 per habit logged (max +20), -5 per overdue task."""
        score = 50
        score += min(completed * 10, 30)
        score += min(habits * 10, 20)
        score -= overdue * 5
        return max(0, min(100, score))

    @staticmethod
    def _score_message(score: int) -> str:
        if score >= 90:
            return "🏆 Outstanding! You're crushing it today!"
        if score >= 75:
            return "🌟 Great job! You're being very productive!"
        if score >= 60:
            return "👍 Good progress! Keep the momentum going!"
        if score >= 40:
            return "💪 Decent start. Try completing a few more tasks!"
        return "🌱 Every step counts. Start with one small task!"

    @staticmethod
    def _trend(current: int, previous: int) -> str:
        if previous == 0:
            return f"+{current} vs last week" if current > 0 else "No change"
        change = round((current - previous) / previous * 100)
        if change > 0:
            return f"+{change}% vs last week"
        if change < 0:
            return f"{change}% vs last week"
        return "Same as last week"
