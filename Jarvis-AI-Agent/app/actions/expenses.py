"""Expense tracking action module.

Track daily expenses, categorize spending, and view summaries.
Expense dates are local calendar dates.
"""

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import BaseAction
from app.db.models import Expense
from app.utils import local_today, money

logger = logging.getLogger(__name__)


def _to_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def period_start(period: str, today: date) -> date:
    """First local date included in a 'today' / 'week' / 'month' period."""
    if period == "today":
        return today
    if period == "week":
        return today - timedelta(days=6)   # today + previous 6 days
    return today - timedelta(days=29)      # last 30 days


class ExpenseAction(BaseAction):
    """Track and manage personal expenses."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def name(self) -> str:
        return "expenses"

    @property
    def description(self) -> str:
        return "Track daily expenses and view spending summaries"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action", "")
        handlers = {
            "add_expense": self._add_expense,
            "list_expenses": self._list_expenses,
            "spending_summary": self._spending_summary,
            "delete_expense": self._delete_expense,
        }
        handler = handlers.get(action)
        if not handler:
            return {"success": False, "message": f"Unknown expense action: {action}"}
        return await handler(params)

    async def _add_expense(self, params: dict) -> dict[str, Any]:
        try:
            amount = round(float(params.get("amount", 0)), 2)
            if amount <= 0:
                return {"success": False, "message": "Amount must be positive"}

            expense_date = _to_date(params["date"]) if params.get("date") else local_today()
            category = (params.get("category") or "general").strip().lower()

            expense = Expense(
                amount=amount,
                category=category,
                description=params.get("description") or "",
                date=expense_date,
            )
            self._session.add(expense)
            await self._session.commit()
            await self._session.refresh(expense)

            return {
                "success": True,
                "message": f"Added expense #{expense.id}: {money(amount)} ({category})",
                "expense_id": expense.id,
                "amount": amount,
                "category": category,
                "date": str(expense_date),
            }
        except (ValueError, TypeError) as e:
            return {"success": False, "message": f"Invalid input: {e}"}
        except Exception as e:
            await self._session.rollback()
            logger.error(f"Failed to add expense: {e}")
            return {"success": False, "message": f"Failed to add expense: {e}"}

    async def _list_expenses(self, params: dict) -> dict[str, Any]:
        try:
            query = select(Expense).order_by(Expense.date.desc(), Expense.id.desc())

            if params.get("category"):
                query = query.where(Expense.category == str(params["category"]).lower())
            if params.get("start_date"):
                query = query.where(Expense.date >= _to_date(params["start_date"]))
            if params.get("end_date"):
                query = query.where(Expense.date <= _to_date(params["end_date"]))

            expenses = (await self._session.execute(query)).scalars().all()
            total = round(sum(e.amount for e in expenses), 2)

            return {
                "success": True,
                "expenses": [
                    {
                        "id": e.id,
                        "amount": e.amount,
                        "category": e.category,
                        "description": e.description,
                        "date": str(e.date),
                    }
                    for e in expenses
                ],
                "total": total,
                "message": f"Found {len(expenses)} expense(s), total {money(total)}",
            }
        except Exception as e:
            logger.error(f"Failed to list expenses: {e}")
            return {"success": False, "message": f"Failed to list expenses: {e}"}

    async def _spending_summary(self, params: dict) -> dict[str, Any]:
        try:
            period = params.get("period") or "month"
            if period not in ("today", "week", "month"):
                period = "month"
            start = period_start(period, local_today())

            query = (
                select(Expense.category, func.sum(Expense.amount).label("total"))
                .where(Expense.date >= start)
                .group_by(Expense.category)
                .order_by(func.sum(Expense.amount).desc())
            )
            rows = (await self._session.execute(query)).all()

            categories = [{"name": row[0], "total": round(row[1], 2)} for row in rows]
            grand_total = round(sum(c["total"] for c in categories), 2)

            return {
                "success": True,
                "period": period,
                "start_date": str(start),
                "categories": categories,
                "grand_total": grand_total,
                "message": f"Total spending ({period}): {money(grand_total)}",
            }
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            return {"success": False, "message": f"Failed to generate summary: {e}"}

    async def _delete_expense(self, params: dict) -> dict[str, Any]:
        try:
            expense_id = int(params.get("expense_id", 0))
            expense = await self._session.get(Expense, expense_id)
            if not expense:
                return {"success": False, "message": f"Expense {expense_id} not found"}

            await self._session.delete(expense)
            await self._session.commit()
            return {"success": True, "message": f"Deleted expense #{expense_id}"}
        except Exception as e:
            await self._session.rollback()
            logger.error(f"Failed to delete expense: {e}")
            return {"success": False, "message": f"Failed to delete expense: {e}"}
