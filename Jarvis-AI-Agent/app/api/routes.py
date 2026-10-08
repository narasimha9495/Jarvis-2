"""REST API routes for Jarvis AI Agent.

The dashboard uses these for its sidebar panels. Chat goes through the
WebSocket, but POST /api/chat offers the same agent for scripts.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import __version__
from app.actions.expenses import ExpenseAction
from app.actions.habits import HabitAction
from app.actions.notes import NoteAction
from app.actions.pomodoro import PomodoroAction
from app.actions.reminders import ReminderAction
from app.actions.system_monitor import SystemMonitorAction
from app.actions.daily_summary import DailySummaryAction
from app.actions.tasks import TaskAction
from app.actions.weather import WeatherAction
from app.config import get_settings
from app.core.agent import Conversation, handle_user_message
from app.core.llm_router import get_llm_router
from app.core.safety import get_safety_engine
from app.db.database import get_db
from app.db.models import Note, Reminder, Task
from app.models.schemas import (
    ChatRequest, ChatResponse, ExpenseCreate, HabitCreate, HealthResponse,
    NoteCreate, NoteResponse, ProviderSwitch, ReminderCreate, ReminderResponse,
    TaskCreate, TaskResponse,
)
from app.utils import to_db_datetime

router = APIRouter()


def _ok_or_404(result: dict) -> dict:
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message", "Not found"))
    return result


def _ok_or_400(result: dict) -> dict:
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("message", "Bad request"))
    return result


# ── Health / providers ──

@router.get("/health", response_model=HealthResponse)
async def health_check():
    llm = get_llm_router()
    return HealthResponse(
        status="ok",
        version=__version__,
        available_providers=llm.get_available_providers(),
        default_provider=llm.default_provider_name if llm.get_available_providers() else None,
        currency_symbol=get_settings().currency_symbol,
    )


@router.get("/providers")
async def list_providers():
    llm = get_llm_router()
    return {
        "providers": llm.get_available_providers(),
        "healthy": await llm.list_available(),
        "default": llm.default_provider_name,
    }


@router.post("/providers/switch")
async def switch_provider(payload: ProviderSwitch):
    """Change the default provider (until the server restarts)."""
    llm = get_llm_router()
    try:
        llm.set_default(payload.provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"message": f"Default provider is now {payload.provider}", "default": payload.provider}


# ── Chat (stateless; one message in, one reply out) ──

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Send one message to the agent. Actions are executed (subject to the
    safety engine); dangerous ones are NOT run here — use the dashboard,
    which can ask you to confirm."""
    llm = get_llm_router()
    conv = Conversation(max_messages=get_settings().max_history_messages)
    reply = await handle_user_message(
        conv, request.message, router=llm, safety=get_safety_engine(), db=db, provider=request.provider
    )
    content = reply.content
    if reply.type == "confirm":
        content += "\n(Confirmation is only available in the dashboard; nothing was changed.)"
    return ChatResponse(
        type=reply.type,
        response=content,
        provider=request.provider or llm.default_provider_name,
        action_taken=reply.action,
        result=reply.result,
    )


# ── Tasks ──

@router.get("/tasks", response_model=List[TaskResponse])
async def list_tasks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Task).order_by(Task.completed, Task.created_at.desc()))
    return result.scalars().all()


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(task_in: TaskCreate, db: AsyncSession = Depends(get_db)):
    task = Task(
        title=task_in.title.strip(),
        description=task_in.description,
        priority=task_in.priority,
        due_date=to_db_datetime(task_in.due_date) if task_in.due_date else None,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


async def _task_or_404(db: AsyncSession, task_id: int) -> Task:
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/tasks/{task_id}/complete", response_model=TaskResponse)
async def complete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    _ok_or_404(await TaskAction(db).execute({"action": "complete_task", "task_id": task_id}))
    return await _task_or_404(db, task_id)


@router.patch("/tasks/{task_id}/reopen", response_model=TaskResponse)
async def reopen_task(task_id: int, db: AsyncSession = Depends(get_db)):
    _ok_or_404(await TaskAction(db).execute({"action": "reopen_task", "task_id": task_id}))
    return await _task_or_404(db, task_id)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    _ok_or_404(await TaskAction(db).execute({"action": "delete_task", "task_id": task_id}))


# ── Reminders ──

@router.get("/reminders", response_model=List[ReminderResponse])
async def list_reminders(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Reminder).order_by(Reminder.triggered, Reminder.remind_at.asc()))
    return result.scalars().all()


@router.post("/reminders", response_model=ReminderResponse, status_code=status.HTTP_201_CREATED)
async def create_reminder(reminder_in: ReminderCreate, db: AsyncSession = Depends(get_db)):
    reminder = Reminder(message=reminder_in.message.strip(), remind_at=to_db_datetime(reminder_in.remind_at))
    db.add(reminder)
    await db.commit()
    await db.refresh(reminder)
    return reminder


@router.post("/reminders/{reminder_id}/dismiss")
async def dismiss_reminder(reminder_id: int, db: AsyncSession = Depends(get_db)):
    return _ok_or_404(await ReminderAction(db).execute({"action": "dismiss_reminder", "reminder_id": reminder_id}))


# ── Notes ──

@router.get("/notes", response_model=List[NoteResponse])
async def list_notes(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Note).order_by(Note.updated_at.desc()))
    return result.scalars().all()


@router.post("/notes", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def create_note(note_in: NoteCreate, db: AsyncSession = Depends(get_db)):
    note = Note(title=note_in.title.strip(), content=note_in.content, tags=note_in.tags)
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: int, db: AsyncSession = Depends(get_db)):
    _ok_or_404(await NoteAction(db).execute({"action": "delete_note", "note_id": note_id}))


# ── Weather ──

@router.get("/weather/{city}")
async def get_weather(city: str):
    result = await WeatherAction().execute({"action": "get_weather", "city": city})
    if not result["success"]:
        code = 404 if result.get("not_found") else 502
        raise HTTPException(status_code=code, detail=result["message"])
    return result


# ── Expenses ──

@router.get("/expenses")
async def list_expenses(category: str | None = None, db: AsyncSession = Depends(get_db)):
    params = {"action": "list_expenses"}
    if category:
        params["category"] = category
    return await ExpenseAction(db).execute(params)


@router.post("/expenses", status_code=status.HTTP_201_CREATED)
async def add_expense(expense_in: ExpenseCreate, db: AsyncSession = Depends(get_db)):
    return _ok_or_400(await ExpenseAction(db).execute({"action": "add_expense", **expense_in.model_dump()}))


@router.get("/expenses/summary")
async def spending_summary(period: str = "month", db: AsyncSession = Depends(get_db)):
    return await ExpenseAction(db).execute({"action": "spending_summary", "period": period})


@router.delete("/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(expense_id: int, db: AsyncSession = Depends(get_db)):
    _ok_or_404(await ExpenseAction(db).execute({"action": "delete_expense", "expense_id": expense_id}))


# ── Habits ──

@router.get("/habits")
async def list_habits(db: AsyncSession = Depends(get_db)):
    return await HabitAction(db).execute({"action": "list_habits"})


@router.post("/habits", status_code=status.HTTP_201_CREATED)
async def create_habit(habit_in: HabitCreate, db: AsyncSession = Depends(get_db)):
    return _ok_or_400(await HabitAction(db).execute({"action": "create_habit", **habit_in.model_dump()}))


@router.get("/habits/report")
async def habit_report(period: str = "week", db: AsyncSession = Depends(get_db)):
    return await HabitAction(db).execute({"action": "habit_report", "period": period})


@router.post("/habits/{habit_id}/log")
async def log_habit(habit_id: int, db: AsyncSession = Depends(get_db)):
    return _ok_or_404(await HabitAction(db).execute({"action": "log_habit", "habit_id": habit_id}))


@router.delete("/habits/{habit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_habit(habit_id: int, db: AsyncSession = Depends(get_db)):
    _ok_or_404(await HabitAction(db).execute({"action": "delete_habit", "habit_id": habit_id}))


# ── Pomodoro ──

class PomodoroStart(BaseModel):
    task: str = "Focus"
    duration: int = Field(default=25, ge=1, le=180)
    break_duration: int = Field(default=5, ge=1, le=60)


@router.post("/pomodoro/start")
async def start_pomodoro(payload: PomodoroStart):
    return _ok_or_400(await PomodoroAction().execute({"action": "start", **payload.model_dump()}))


@router.get("/pomodoro/{session_id}")
async def pomodoro_status(session_id: str):
    return _ok_or_404(await PomodoroAction().execute({"action": "status", "session_id": session_id}))


@router.post("/pomodoro/{session_id}/complete")
async def complete_pomodoro(session_id: str):
    return _ok_or_404(await PomodoroAction().execute({"action": "complete", "session_id": session_id}))


@router.delete("/pomodoro/{session_id}")
async def stop_pomodoro(session_id: str):
    return _ok_or_404(await PomodoroAction().execute({"action": "stop", "session_id": session_id}))


# ── System / summaries ──

@router.get("/system/status")
async def system_status():
    return await SystemMonitorAction().execute({"action": "system_status"})


@router.get("/system/processes")
async def top_processes():
    return await SystemMonitorAction().execute({"action": "top_processes"})


@router.get("/summary/daily")
async def daily_summary(db: AsyncSession = Depends(get_db)):
    return await DailySummaryAction(db).execute({"action": "generate_summary"})


@router.get("/summary/weekly")
async def weekly_report(db: AsyncSession = Depends(get_db)):
    return await DailySummaryAction(db).execute({"action": "weekly_report"})
