"""Background job that fires reminders when they come due.

Every few seconds it looks for reminders whose time has passed, marks them
triggered and pushes them to every open dashboard (which shows them in chat
and as a desktop notification if the browser allows it).

If no dashboard is open when a reminder comes due, it waits: it fires the
next time a dashboard is connected, so reminders aren't silently lost.
"""

import asyncio
import logging

from sqlalchemy import select

from app.core.notifications import manager
from app.db import database
from app.db.models import Reminder
from app.utils import from_db_datetime, utc_now

logger = logging.getLogger(__name__)


async def fire_due_reminders(session_factory=None) -> list[dict]:
    """Fire every due, untriggered reminder once. Returns what was sent."""
    if manager.count == 0:
        return []

    session_factory = session_factory or database.async_session_maker
    now = utc_now().replace(tzinfo=None)
    fired = []
    async with session_factory() as session:
        due = (
            await session.execute(
                select(Reminder)
                .where(Reminder.triggered == False, Reminder.remind_at <= now)  # noqa: E712
                .order_by(Reminder.remind_at)
            )
        ).scalars().all()

        for reminder in due:
            message = {
                "type": "reminder",
                "content": f"🔔 Reminder: {reminder.message}",
                "reminder_id": reminder.id,
                "remind_at": from_db_datetime(reminder.remind_at).isoformat(),
            }
            if await manager.broadcast(message):
                reminder.triggered = True
                fired.append(message)
        await session.commit()

    if fired:
        logger.info(f"Fired {len(fired)} reminder(s)")
    return fired


async def run_reminder_loop(interval_seconds: int) -> None:
    """Run forever (cancelled on shutdown)."""
    while True:
        try:
            await fire_due_reminders()
        except Exception as e:  # never let one bad cycle kill the loop
            logger.error(f"Reminder check failed: {e}")
        await asyncio.sleep(interval_seconds)
