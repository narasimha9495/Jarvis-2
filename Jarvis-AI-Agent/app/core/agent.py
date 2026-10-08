"""The Jarvis agent: one place that turns a user message into a reply.

Both the WebSocket and the REST /api/chat endpoint use this module, so
there is exactly ONE system prompt and ONE list of actions. The system
prompt is generated from ACTIONS below, so the LLM can never be told
about an action the backend doesn't actually support.

Flow for each user message:
    user text ─▶ LLM (with recent history) ─▶ reply
        reply is plain text?  ─▶ send it as chat
        reply is an action?   ─▶ SafetyEngine
             blocked           ─▶ error
             needs confirmation ─▶ stored server-side under a random id,
                                   client gets {type: confirm, confirm_id}
             allowed           ─▶ run action ─▶ readable result text
"""

from __future__ import annotations

import importlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm_router import LLMRouter
from app.core.safety import SafetyEngine
from app.utils import local_now_description, money, parse_json_response, sanitize_input, truncate

logger = logging.getLogger(__name__)


# ── Action registry ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ActionSpec:
    module: str          # e.g. "app.actions.tasks"
    cls: str             # e.g. "TaskAction"
    needs_db: bool
    params: str          # parameter description shown to the LLM
    sub_action: str = ""  # name the action class expects, if different


_T = ("app.actions.tasks", "TaskAction", True)
_R = ("app.actions.reminders", "ReminderAction", True)
_N = ("app.actions.notes", "NoteAction", True)
_E = ("app.actions.expenses", "ExpenseAction", True)
_H = ("app.actions.habits", "HabitAction", True)
_P = ("app.actions.pomodoro", "PomodoroAction", False)
_S = ("app.actions.system_monitor", "SystemMonitorAction", False)
_D = ("app.actions.daily_summary", "DailySummaryAction", True)

ACTIONS: dict[str, ActionSpec] = {
    # Tasks
    "create_task": ActionSpec(*_T, '{"title": str, "description": str, "priority": "low"|"medium"|"high", "due_date": ISO datetime (optional)}'),
    "list_tasks": ActionSpec(*_T, '{"completed": bool (optional)}'),
    "complete_task": ActionSpec(*_T, '{"task_id": int}'),
    "reopen_task": ActionSpec(*_T, '{"task_id": int}'),
    "delete_task": ActionSpec(*_T, '{"task_id": int}'),
    # Reminders
    "create_reminder": ActionSpec(*_R, '{"message": str, "remind_at": ISO local datetime e.g. "2026-12-25T10:00:00"}'),
    "list_reminders": ActionSpec(*_R, "{}"),
    "dismiss_reminder": ActionSpec(*_R, '{"reminder_id": int}'),
    # Notes
    "create_note": ActionSpec(*_N, '{"title": str, "content": str, "tags": "comma,separated"}'),
    "list_notes": ActionSpec(*_N, '{"tag": str (optional)}'),
    "search_notes": ActionSpec(*_N, '{"keyword": str}'),
    "delete_note": ActionSpec(*_N, '{"note_id": int}'),
    # Web
    "search_web": ActionSpec("app.actions.search", "SearchAction", False, '{"query": str}'),
    "get_weather": ActionSpec("app.actions.weather", "WeatherAction", False, '{"city": str}'),
    # Expenses
    "add_expense": ActionSpec(*_E, '{"amount": number, "category": str, "description": str, "date": "YYYY-MM-DD" (optional)}'),
    "list_expenses": ActionSpec(*_E, '{"category": str (optional)}'),
    "spending_summary": ActionSpec(*_E, '{"period": "today"|"week"|"month"}'),
    "delete_expense": ActionSpec(*_E, '{"expense_id": int}'),
    # Pomodoro
    "start_pomodoro": ActionSpec(*_P, '{"task": str, "duration": minutes (optional, default 25)}', "start"),
    "pomodoro_status": ActionSpec(*_P, "{}", "status"),
    "complete_pomodoro": ActionSpec(*_P, "{}", "complete"),
    "stop_pomodoro": ActionSpec(*_P, "{}", "stop"),
    "pomodoro_stats": ActionSpec(*_P, "{}", "stats"),
    # Habits
    "create_habit": ActionSpec(*_H, '{"name": str}'),
    "log_habit": ActionSpec(*_H, '{"name": str} or {"habit_id": int}'),
    "list_habits": ActionSpec(*_H, "{}"),
    "habit_report": ActionSpec(*_H, '{"period": "week"|"month"}'),
    "delete_habit": ActionSpec(*_H, '{"habit_id": int}'),
    # System
    "system_status": ActionSpec(*_S, "{}"),
    "cpu_alert": ActionSpec(*_S, '{"threshold": int (optional)}'),
    "disk_alert": ActionSpec(*_S, '{"threshold": int (optional)}'),
    "top_processes": ActionSpec(*_S, "{}"),
    # Summaries
    "generate_summary": ActionSpec(*_D, "{}"),
    "weekly_report": ActionSpec(*_D, "{}"),
}


def build_system_prompt() -> str:
    """System prompt generated from ACTIONS (so it can never drift)."""
    action_lines = "\n".join(f"- {name}: {spec.params}" for name, spec in ACTIONS.items())
    return f"""You are Jarvis, a concise personal productivity assistant.
Current local date/time: {local_now_description()}.

When the user wants something DONE (create, list, log, search, check...), reply with
ONLY a JSON object, no other text:
{{"action": "action_name", "params": {{...}}}}

Available actions:
{action_lines}

Rules:
- Use exactly one action per reply, and only the actions listed above.
- Write dates/times as ISO 8601 in the user's LOCAL time (no time-zone suffix),
  resolving words like "tomorrow" or "next Monday" using the current date above.
- Messages starting with "[Action result]" are the real results of your previous
  actions. Use the ids in them (e.g. task #3) when the user refers to an item.
- Never repeat an action that already has a result unless the user asks again.
- If the request is unclear or just conversation, reply normally in plain text."""


async def execute_action(action_name: str, params: dict, db: Optional[AsyncSession]) -> dict:
    """Run a registered action and return a result dict with 'success' and 'message'."""
    spec = ACTIONS.get(action_name)
    if spec is None:
        return {"success": False, "message": f"Unknown action: {action_name}"}
    if not isinstance(params, dict):
        params = {}

    try:
        action_class = getattr(importlib.import_module(spec.module), spec.cls)
        instance = action_class(db) if spec.needs_db else action_class()
        result = await instance.execute({**params, "action": spec.sub_action or action_name})
    except Exception as e:
        logger.exception(f"Action '{action_name}' crashed")
        return {"success": False, "message": f"Action '{action_name}' failed: {e}"}

    # Normalize: every result has success + message
    result = dict(result or {})
    result.setdefault("success", False)
    if "message" not in result:
        result["message"] = result.get("error") or ("Done." if result["success"] else "Failed.")
    return result


# ── Turning results into readable replies ──────────────────────────────────


def _bullets(lines: list[str], empty: str) -> str:
    return "\n".join(lines) if lines else empty


def format_result(action: str, result: dict) -> str:
    """Human-readable text for an action result (shown in chat)."""
    if not result.get("success"):
        return f"⚠️ {result.get('message', 'Something went wrong.')}"

    msg = result.get("message", "Done.")

    if action == "list_tasks":
        lines = [
            f"{'✅' if t['completed'] else '⬜'} #{t['id']} **{t['title']}** ({t['priority']})"
            for t in result.get("tasks", [])
        ]
        return _bullets(lines, "No tasks yet.")

    if action == "list_reminders":
        lines = [
            f"{'✔️' if r['triggered'] else '🔔'} #{r['id']} {r['message']} — {r['remind_at']}"
            for r in result.get("reminders", [])
        ]
        return _bullets(lines, "No reminders.")

    if action in ("list_notes", "search_notes"):
        lines = [
            f"📝 #{n['id']} **{n['title']}**: {truncate(n.get('content') or '', 80)}"
            for n in result.get("notes", [])
        ]
        return _bullets(lines, "No notes found.")

    if action == "search_web":
        lines = [
            f"{i}. **{r['title']}**\n{r['url']}\n{truncate(r.get('snippet') or '', 160)}"
            for i, r in enumerate(result.get("results", []), 1)
        ]
        return _bullets(lines, msg)

    if action == "get_weather":
        cur = result.get("current", {})
        lines = [
            f"🌤️ **{result.get('city')}**: {cur.get('temp')}°C, {cur.get('condition')} "
            f"(humidity {cur.get('humidity')}%, wind {cur.get('wind_speed')} km/h)"
        ]
        lines += [
            f"• {d['date']}: {d['min_temp']}–{d['max_temp']}°C, {d['condition']}"
            for d in result.get("forecast", [])
        ]
        return "\n".join(lines)

    if action == "list_expenses":
        lines = [
            f"💰 #{e['id']} {money(e['amount'])} — {e['category']}"
            + (f" ({e['description']})" if e.get("description") else "")
            + f" · {e['date']}"
            for e in result.get("expenses", [])
        ]
        return _bullets(lines, "No expenses yet.") + f"\n**Total: {money(result.get('total', 0))}**"

    if action == "spending_summary":
        lines = [f"• {c['name']}: {money(c['total'])}" for c in result.get("categories", [])]
        return f"**{msg}**\n" + _bullets(lines, "Nothing spent in this period.")

    if action == "list_habits":
        lines = [
            f"{'✅' if h.get('done_today') else '⬜'} #{h['id']} **{h['name']}** — "
            f"🔥 {h['current_streak']} day streak, {h['total_completions']} total"
            for h in result.get("habits", [])
        ]
        return _bullets(lines, "No habits yet.")

    if action == "habit_report":
        lines = [
            f"• **{h['name']}**: {h['completed_days']}/{h['total_days']} days "
            f"({h['completion_rate']}%), streak {h['streak']}"
            for h in result.get("habits", [])
        ]
        return f"**{msg}**\n" + _bullets(lines, "No habits yet.")

    if action == "system_status":
        bat = result.get("battery")
        bat_text = f"\n🔋 Battery: {bat['percent']}% ({bat['time_left']})" if bat else ""
        return (
            f"🖥️ CPU: {result['cpu_percent']}% ({result['cpu_count']} cores)\n"
            f"🧠 RAM: {result['memory']['used_gb']}/{result['memory']['total_gb']} GB ({result['memory']['percent']}%)\n"
            f"💾 Disk: {result['disk']['free_gb']} GB free ({result['disk']['percent']}% used)"
            f"{bat_text}"
        )

    if action == "top_processes":
        lines = [
            f"• {p['name']} — {p['cpu_percent']}% CPU, {p['memory_mb']} MB"
            for p in result.get("processes", [])
        ]
        return _bullets(lines, msg)

    if action == "generate_summary":
        s = result.get("summary", {})
        return (
            f"📊 **Productivity score: {result.get('productivity_score')}/100** — {msg}\n"
            f"• Tasks: {s.get('tasks_completed')} completed, {s.get('tasks_created')} created, "
            f"{s.get('overdue_tasks')} overdue\n"
            f"• Habits logged: {s.get('habits_logged')}\n"
            f"• Notes: {s.get('notes_created')} · Reminders: {s.get('reminders_set')}\n"
            f"• Spent today: {money(s.get('expenses_total') or 0)}"
        )

    return msg


# ── Conversation + reply handling ──────────────────────────────────────────

CONFIRM_TTL_SECONDS = 300


@dataclass
class AgentReply:
    """What the agent wants to send back to the client."""

    type: str  # "chat" | "action" | "confirm" | "error"
    content: str
    action: Optional[str] = None
    result: Optional[dict] = None
    confirm_id: Optional[str] = None


@dataclass
class _Pending:
    action: str
    params: dict
    created: float


@dataclass
class Conversation:
    """Per-connection state: recent history and actions awaiting confirmation."""

    max_messages: int = 20
    history: list[dict[str, str]] = field(default_factory=list)
    pending: dict[str, _Pending] = field(default_factory=dict)

    def add(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        if len(self.history) > self.max_messages:
            self.history = self.history[-self.max_messages:]

    def llm_messages(self) -> list[dict[str, str]]:
        """System prompt + recent history, with same-role neighbours merged
        (some providers reject two user messages in a row)."""
        merged: list[dict[str, str]] = []
        for m in self.history:
            if merged and merged[-1]["role"] == m["role"]:
                merged[-1] = {"role": m["role"], "content": merged[-1]["content"] + "\n\n" + m["content"]}
            else:
                merged.append(dict(m))
        # History must start with a user turn after trimming
        while merged and merged[0]["role"] != "user":
            merged.pop(0)
        return [{"role": "system", "content": build_system_prompt()}] + merged

    def add_pending(self, action: str, params: dict) -> str:
        self._expire()
        confirm_id = secrets.token_urlsafe(16)
        self.pending[confirm_id] = _Pending(action, params, time.monotonic())
        return confirm_id

    def pop_pending(self, confirm_id: Optional[str]) -> Optional[_Pending]:
        self._expire()
        return self.pending.pop(confirm_id, None) if confirm_id else None

    def _expire(self) -> None:
        now = time.monotonic()
        for key in [k for k, p in self.pending.items() if now - p.created > CONFIRM_TTL_SECONDS]:
            del self.pending[key]


def _record_result(conv: Conversation, action: str, text: str) -> None:
    conv.add("user", f"[Action result] {action}: {truncate(text, 1500)}")


async def handle_user_message(
    conv: Conversation,
    text: str,
    *,
    router: LLMRouter,
    safety: SafetyEngine,
    db: Optional[AsyncSession],
    provider: Optional[str] = None,
) -> AgentReply:
    """Process one user message and return the reply to send."""
    text = sanitize_input(text or "")
    if not text:
        return AgentReply("error", "Please type a message.")
    if len(text) > 4000:
        return AgentReply("error", "That message is too long (max 4000 characters).")

    conv.add("user", text)
    try:
        response_text = await router.generate(messages=conv.llm_messages(), provider=provider or None)
    except Exception as e:
        conv.history.pop()  # don't keep a turn the model never answered
        logger.error(f"LLM error: {e}")
        return AgentReply("error", f"AI provider error: {e}")

    action_data = parse_json_response(response_text)
    if not action_data:
        conv.add("assistant", response_text)
        return AgentReply("chat", response_text or "(no response)")

    action = str(action_data.get("action") or "")
    params = action_data.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    conv.add("assistant", json.dumps({"action": action, "params": params}))

    check = await safety.check_action(action, params)
    if not check.allowed:
        _record_result(conv, action, f"blocked: {check.message}")
        return AgentReply("error", check.message, action=action)

    if action not in ACTIONS:
        _record_result(conv, action, "unknown action, not executed")
        return AgentReply("error", f"I tried an action I don't support ('{action}'). Please rephrase.", action=action)

    if check.requires_confirmation:
        confirm_id = conv.add_pending(action, params)
        pretty = ", ".join(f"{k}={v}" for k, v in params.items()) or "no parameters"
        return AgentReply(
            "confirm",
            f"Jarvis wants to run **{action}** ({pretty}). This can't be undone. Continue?",
            action=action,
            confirm_id=confirm_id,
        )

    result = await execute_action(action, params, db)
    reply_text = format_result(action, result)
    _record_result(conv, action, reply_text)
    return AgentReply("action" if result["success"] else "error", reply_text, action=action, result=result)


async def handle_confirmation(
    conv: Conversation,
    confirm_id: Optional[str],
    approved: bool,
    *,
    db: Optional[AsyncSession],
) -> AgentReply:
    """Run (or cancel) an action the server previously asked to confirm.

    Only ids issued by this conversation work — a client cannot invent an
    action or change its parameters.
    """
    pending = conv.pop_pending(confirm_id)
    if pending is None:
        return AgentReply("error", "That confirmation has expired or is invalid. Please ask again.")

    if not approved:
        _record_result(conv, pending.action, "cancelled by the user")
        return AgentReply("chat", "Okay, cancelled.", action=pending.action)

    result = await execute_action(pending.action, pending.params, db)
    reply_text = format_result(pending.action, result)
    _record_result(conv, pending.action, reply_text)
    return AgentReply("action" if result["success"] else "error", reply_text, action=pending.action, result=result)
