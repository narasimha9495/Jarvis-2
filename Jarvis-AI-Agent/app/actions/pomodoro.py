"""Pomodoro focus timer.

Sessions are kept in memory (they reset when the server restarts).
Accepts both short sub-action names ("start") and the chat names
("start_pomodoro"). When no session_id is given, the most recent
session is used, so "how long is left?" works from chat.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.actions.base import BaseAction

_ALIASES = {
    "start_pomodoro": "start",
    "pomodoro_status": "status",
    "complete_pomodoro": "complete",
    "stop_pomodoro": "stop",
    "pomodoro_stats": "stats",
}


class PomodoroAction(BaseAction):
    """Pomodoro focus timer for productivity."""

    # Class-level so every request/connection sees the same sessions
    _sessions: Dict[str, Dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "pomodoro"

    @property
    def description(self) -> str:
        return "Pomodoro focus timer for productivity"

    def _resolve(self, params: Dict[str, Any]) -> Optional[str]:
        session_id = params.get("session_id")
        if session_id:
            return session_id if session_id in self._sessions else None
        if not self._sessions:
            return None
        # Latest session (dicts keep insertion order)
        return next(reversed(self._sessions))

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        action = _ALIASES.get(params.get("action"), params.get("action"))

        if action == "start":
            task = params.get("task") or "Focus"
            try:
                duration = int(params.get("duration") or 25)
                break_duration = int(params.get("break_duration") or 5)
            except (TypeError, ValueError):
                return {"success": False, "message": "duration must be a number of minutes"}
            if not (1 <= duration <= 180):
                return {"success": False, "message": "duration must be between 1 and 180 minutes"}

            session_id = uuid.uuid4().hex[:8]
            self._sessions[session_id] = {
                "task": task,
                "start_time": datetime.now(timezone.utc),
                "duration": duration,
                "break_duration": break_duration,
                "status": "focusing",
                "completed_pomodoros": 0,
            }
            return {
                "success": True,
                "session_id": session_id,
                "task": task,
                "duration": duration,
                "message": f"🍅 Pomodoro started for '{task}' — focus for {duration} minutes.",
            }

        if action == "stats":
            total = sum(s.get("completed_pomodoros", 0) for s in self._sessions.values())
            return {
                "success": True,
                "total_completed_pomodoros": total,
                "active_sessions": len(self._sessions),
                "message": f"{total} pomodoro(s) completed across {len(self._sessions)} session(s).",
            }

        if action in ("status", "complete", "stop"):
            session_id = self._resolve(params)
            if not session_id:
                return {"success": False, "message": "No active pomodoro session."}
            session = self._sessions[session_id]

            if action == "status":
                elapsed = datetime.now(timezone.utc) - session["start_time"]
                elapsed_minutes = int(elapsed.total_seconds() // 60)
                phase = session["duration"] if session["status"] == "focusing" else session["break_duration"]
                remaining = max(0, phase - elapsed_minutes)
                return {
                    "success": True,
                    "session_id": session_id,
                    "task": session["task"],
                    "elapsed_minutes": elapsed_minutes,
                    "remaining_minutes": remaining,
                    "status": session["status"],
                    "message": f"'{session['task']}': {session['status']}, {remaining} min remaining.",
                }

            if action == "complete":
                session["completed_pomodoros"] += 1
                session["status"] = "break"
                session["start_time"] = datetime.now(timezone.utc)
                return {
                    "success": True,
                    "session_id": session_id,
                    "completed_pomodoros": session["completed_pomodoros"],
                    "message": f"Pomodoro completed! Take a {session['break_duration']} minute break.",
                }

            # stop
            del self._sessions[session_id]
            return {"success": True, "session_id": session_id, "message": f"Pomodoro for '{session['task']}' stopped."}

        return {"success": False, "message": f"Unknown pomodoro action: {params.get('action')}"}
