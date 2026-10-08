"""End-to-end regression tests: browser → WebSocket → agent → action → reply.

Each test here reproduces a bug found in review, so it can't come back.
The LLM is replaced by a scripted fake, so no API keys or network are needed.
"""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.websockets import WebSocketDisconnect

import app.api.routes as routes_module
import app.api.websocket as ws_module
from app.config import Settings
from app.core import agent
from app.core.llm_router import LLMRouter
from app.core.notifications import manager
from app.core.reminder_scheduler import fire_due_reminders
from app.core.safety import ACTION_RISK_MAP
from app.db import database
from app.db.database import get_db, init_db
from app.db.models import Base, HabitLog, Reminder, Task
from app.main import app
from app.providers.base import BaseLLMProvider
from app.utils import local_today, parse_json_response, parse_user_datetime


# ── Fakes and fixtures ──


class ScriptedLLM(BaseLLMProvider):
    """Returns queued replies in order and records every conversation it saw."""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls: list[list[dict]] = []

    async def chat(self, messages):
        self.calls.append([dict(m) for m in messages])
        return self.replies.pop(0) if self.replies else "ok"

    async def chat_stream(self, messages):
        yield await self.chat(messages)

    async def health_check(self):
        return True


def act(name, **params):
    return json.dumps({"action": name, "params": params})


@pytest.fixture
def env(tmp_path, monkeypatch):
    """App wired to a temp database and a scripted LLM."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(init_db(engine))

    llm = ScriptedLLM()
    router = LLMRouter()
    router.register("fake", llm)
    router.default_provider_name = "fake"

    monkeypatch.setattr(database, "async_session_maker", maker)
    monkeypatch.setattr(ws_module, "get_llm_router", lambda: router)
    monkeypatch.setattr(routes_module, "get_llm_router", lambda: router)

    async def override_get_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db

    class Env:
        pass

    e = Env()
    e.client = TestClient(app)  # not used as a context manager → no lifespan / real DB
    e.llm, e.maker, e.engine = llm, maker, engine

    def run(coro_fn):
        async def go():
            async with maker() as s:
                return await coro_fn(s)
        return asyncio.run(go())

    e.db = run
    yield e
    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


def ws_chat(ws, text):
    ws.send_text(json.dumps({"type": "chat", "content": text}))
    return json.loads(ws.receive_text())


def open_ws(client, **kw):
    ws = client.websocket_connect("/ws", **kw)
    conn = ws.__enter__()
    welcome = json.loads(conn.receive_text())
    assert welcome["type"] == "welcome"
    return ws, conn


# ── 1-3: the app could not chat at all ──


def test_dashboard_loads_its_script(env):
    html = env.client.get("/").text
    assert 'src="/static/js/app.js"' in html
    assert env.client.get("/static/js/app.js").status_code == 200


def test_router_built_from_settings_has_providers():
    router = LLMRouter(Settings(gemini_api_key="k", openai_api_key="", default_llm_provider="gemini"))
    assert {"gemini", "ollama"} <= set(router.get_available_providers())
    assert router.default_provider_name == "gemini"


def test_router_falls_back_when_default_has_no_key():
    router = LLMRouter(Settings(gemini_api_key="", openai_api_key="", default_llm_provider="gemini"))
    assert router.default_provider_name == "ollama"


def test_health_reports_providers(env):
    data = env.client.get("/api/health").json()
    assert data["available_providers"] == ["fake"]
    assert data["default_provider"] == "fake"


def test_plain_chat_reply_reaches_client(env):
    env.llm.replies = ["Hello! How can I help?"]
    ws, conn = open_ws(env.client)
    try:
        reply = ws_chat(conn, "hi")
    finally:
        ws.__exit__(None, None, None)
    assert reply == {"type": "chat", "content": "Hello! How can I help?"}


def test_llm_errors_are_sent_as_error_type(env, monkeypatch):
    async def boom(messages):
        raise RuntimeError("quota exceeded")
    monkeypatch.setattr(env.llm, "chat", boom)
    ws, conn = open_ws(env.client)
    try:
        reply = ws_chat(conn, "hi")
    finally:
        ws.__exit__(None, None, None)
    assert reply["type"] == "error" and "quota exceeded" in reply["content"]


# ── 4: action results were never shown ──


def test_action_result_is_readable_text(env):
    env.llm.replies = [act("create_task", title="Buy milk", priority="high"), act("list_tasks")]
    ws, conn = open_ws(env.client)
    try:
        created = ws_chat(conn, "add task buy milk")
        listed = ws_chat(conn, "show my tasks")
    finally:
        ws.__exit__(None, None, None)

    assert created["type"] == "action" and "Buy milk" in created["content"]
    assert listed["type"] == "action"
    assert "Buy milk" in listed["content"] and "#1" in listed["content"]
    assert not listed["content"].startswith("Executed")


# ── 5: history was flattened, so earlier actions could repeat ──


def test_history_keeps_roles_and_results(env):
    env.llm.replies = [act("add_expense", amount=200, category="food"), "It's sunny."]
    ws, conn = open_ws(env.client)
    try:
        ws_chat(conn, "add expense 200 for lunch")
        ws_chat(conn, "how's the weather vibe")
    finally:
        ws.__exit__(None, None, None)

    second_call = env.llm.calls[1]
    roles = [m["role"] for m in second_call]
    assert roles[0] == "system"
    assert "assistant" in roles                      # the model sees its own earlier reply
    joined = "\n".join(m["content"] for m in second_call)
    assert "[Action result] add_expense" in joined   # ...and what that action did
    assert second_call[-1]["content"].endswith("how's the weather vibe")


def test_history_is_bounded():
    conv = agent.Conversation(max_messages=4)
    for i in range(10):
        conv.add("user", f"m{i}")
    assert len(conv.history) == 4


# ── 6: confirmations could be forged by the client ──


def test_forged_confirmation_is_rejected(env):
    env.client.post("/api/tasks", json={"title": "keep me"})
    ws, conn = open_ws(env.client)
    try:
        conn.send_text(json.dumps({
            "type": "confirm", "confirm_id": "made-up", "approved": True,
            "action": "delete_task", "params": {"task_id": 1},
        }))
        reply = json.loads(conn.receive_text())
    finally:
        ws.__exit__(None, None, None)

    assert reply["type"] == "error"
    assert len(env.client.get("/api/tasks").json()) == 1


def test_real_confirmation_flow(env):
    env.client.post("/api/tasks", json={"title": "delete me"})
    env.client.post("/api/tasks", json={"title": "keep me"})
    env.llm.replies = [act("delete_task", task_id=1), act("delete_task", task_id=2)]

    ws, conn = open_ws(env.client)
    try:
        ask = ws_chat(conn, "delete task 1")
        assert ask["type"] == "confirm" and ask["confirm_id"]
        assert len(env.client.get("/api/tasks").json()) == 2   # nothing deleted yet

        conn.send_text(json.dumps({"type": "confirm", "confirm_id": ask["confirm_id"], "approved": True}))
        done = json.loads(conn.receive_text())
        assert done["type"] == "action"

        # A confirm id works only once
        conn.send_text(json.dumps({"type": "confirm", "confirm_id": ask["confirm_id"], "approved": True}))
        assert json.loads(conn.receive_text())["type"] == "error"

        # Cancelling leaves the task alone
        ask2 = ws_chat(conn, "delete task 2")
        conn.send_text(json.dumps({"type": "confirm", "confirm_id": ask2["confirm_id"], "approved": False}))
        assert "cancel" in json.loads(conn.receive_text())["content"].lower()
    finally:
        ws.__exit__(None, None, None)

    titles = [t["title"] for t in env.client.get("/api/tasks").json()]
    assert titles == ["keep me"]


def test_blocked_action_is_never_run(env):
    env.llm.replies = [act("run_command", cmd="rm -rf /")]
    ws, conn = open_ws(env.client)
    try:
        reply = ws_chat(conn, "wipe my disk")
    finally:
        ws.__exit__(None, None, None)
    assert reply["type"] == "error" and "blocked" in reply["content"]


# ── 7: other websites could drive Jarvis through the browser ──


def test_websocket_from_other_site_is_refused(env):
    with pytest.raises(WebSocketDisconnect) as exc:
        with env.client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()
    assert exc.value.code == 1008


def test_websocket_from_same_site_is_allowed(env):
    ws, conn = open_ws(env.client, headers={"origin": "http://testserver"})
    ws.__exit__(None, None, None)


def test_cross_site_writes_are_blocked(env):
    bad = env.client.post("/api/tasks", json={"title": "x"}, headers={"origin": "https://evil.example"})
    assert bad.status_code == 403
    good = env.client.post("/api/tasks", json={"title": "x"}, headers={"origin": "http://testserver"})
    assert good.status_code == 201


def test_default_host_is_localhost_only():
    assert Settings().app_host == "127.0.0.1"


# ── 8: action names the LLM was told about didn't work ──


@pytest.mark.parametrize("name", sorted(agent.ACTIONS))
def test_every_advertised_action_is_routed(env, name):
    """No action in the prompt may come back as 'Unknown action'."""
    params = {"session_id": "none"} if "pomodoro" in name else {}
    result = env.db(lambda s: agent.execute_action(name, params, s))
    assert "unknown" not in result["message"].lower(), result


def test_every_advertised_action_has_a_risk_level():
    assert set(agent.ACTIONS) <= set(ACTION_RISK_MAP)


def test_pomodoro_from_chat(env):
    started = env.db(lambda s: agent.execute_action("start_pomodoro", {"task": "study"}, s))
    assert started["success"]
    status = env.db(lambda s: agent.execute_action("pomodoro_status", {}, s))  # no id → latest
    assert status["success"] and status["task"] == "study"


def test_search_notes_accepts_query_and_delete_note_works(env):
    env.db(lambda s: agent.execute_action("create_note", {"title": "Python tips", "content": "x"}, s))
    found = env.db(lambda s: agent.execute_action("search_notes", {"query": "python"}, s))
    assert found["success"] and len(found["notes"]) == 1
    deleted = env.db(lambda s: agent.execute_action("delete_note", {"note_id": 1}, s))
    assert deleted["success"]


def test_weather_unknown_city_is_404_not_crash(env, monkeypatch):
    from app.actions.weather import WeatherAction

    async def not_found(self, params):
        return {"success": False, "not_found": True, "message": "City 'x' not found."}

    monkeypatch.setattr(WeatherAction, "execute", not_found)
    assert env.client.get("/api/weather/zzqqxx").status_code == 404


# ── 9: streaks, timezones and summaries ──


def test_streak_shows_until_today_is_logged(env):
    created = env.db(lambda s: agent.execute_action("create_habit", {"name": "Run"}, s))

    async def add_logs(s):
        for d in (1, 2, 3):
            s.add(HabitLog(habit_id=created["habit_id"], completed_date=local_today() - timedelta(days=d)))
        await s.commit()

    env.db(add_logs)
    habits = env.db(lambda s: agent.execute_action("list_habits", {}, s))["habits"]
    assert habits[0]["current_streak"] == 3 and habits[0]["done_today"] is False

    logged = env.db(lambda s: agent.execute_action("log_habit", {"name": "run"}, s))  # by name, any case
    assert logged["current_streak"] == 4


def test_old_task_completed_today_counts(env):
    async def old_task(s):
        s.add(Task(title="old", created_at=datetime(2020, 1, 1)))
        await s.commit()

    env.db(old_task)
    env.db(lambda s: agent.execute_action("complete_task", {"task_id": 1}, s))
    summary = env.db(lambda s: agent.execute_action("generate_summary", {}, s))
    assert summary["summary"]["tasks_completed"] == 1


def test_local_times_are_stored_as_utc():
    local = datetime(2026, 12, 25, 10, 0)
    stored = parse_user_datetime("2026-12-25T10:00:00")
    assert stored == local.astimezone().astimezone(timezone.utc).replace(tzinfo=None)
    assert parse_user_datetime("2026-12-25T10:00:00Z") == datetime(2026, 12, 25, 10, 0)


def test_api_datetimes_carry_utc_offset(env):
    data = env.client.post("/api/reminders", json={"message": "m", "remind_at": "2026-12-25T10:00:00Z"}).json()
    assert data["remind_at"].endswith(("Z", "+00:00"))  # explicit UTC either way
    assert data["remind_at"].startswith("2026-12-25T10:00:00")


def test_task_priority_is_normalized(env):
    for given, expected in [(None, "medium"), ("High", "high"), (1, "high"), ("urgent", "high"), ("weird", "medium")]:
        params = {"title": "t"} if given is None else {"title": "t", "priority": given}
        result = env.db(lambda s: agent.execute_action("create_task", params, s))
        assert result["task"]["priority"] == expected, given


def test_task_can_be_reopened(env):
    task_id = env.client.post("/api/tasks", json={"title": "t"}).json()["id"]
    assert env.client.patch(f"/api/tasks/{task_id}/complete").json()["completed"] is True
    reopened = env.client.patch(f"/api/tasks/{task_id}/reopen").json()
    assert reopened["completed"] is False and reopened["completed_at"] is None


# ── 10: reminders never fired ──


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_text(self, data):
        self.sent.append(json.loads(data))


def test_due_reminder_fires_once(env):
    async def add(s):
        s.add(Reminder(message="due", remind_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)))
        s.add(Reminder(message="later", remind_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)))
        await s.commit()

    env.db(add)
    sock = FakeSocket()
    manager.add(sock)
    try:
        first = asyncio.run(fire_due_reminders(env.maker))
        second = asyncio.run(fire_due_reminders(env.maker))
    finally:
        manager.remove(sock)

    assert [m["content"] for m in first] == ["🔔 Reminder: due"]
    assert second == []
    assert sock.sent[0]["type"] == "reminder"


def test_reminder_waits_when_no_dashboard_is_open(env):
    async def add(s):
        s.add(Reminder(message="due", remind_at=datetime(2020, 1, 1)))
        await s.commit()

    env.db(add)
    assert asyncio.run(fire_due_reminders(env.maker)) == []

    async def still_pending(s):
        return (await s.get(Reminder, 1)).triggered

    assert env.db(still_pending) is False


# ── Parsing, search and DB upgrade ──


def test_action_json_inside_prose_is_found():
    assert parse_json_response('Sure! {"action": "list_tasks", "params": {}} Done.')["action"] == "list_tasks"
    assert parse_json_response('```json\n{"action": "x", "params": {"a": {"b": 1}}}\n```')["params"] == {"a": {"b": 1}}
    assert parse_json_response("I can't do {that} sorry") is None


def test_duckduckgo_parser():
    from app.actions.search import parse_results

    html = """
    <div class="result results_links web-result">
      <h2 class="result__title">
        <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=abc">Welcome to <b>Python</b>.org</a>
      </h2>
      <a class="result__url" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F">www.python.org</a>
      <a class="result__snippet" href="#">The official home of the <b>Python</b> Programming Language</a>
    </div>
    <div class="result result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a></div>
    """
    results = parse_results(html)
    assert results == [{
        "title": "Welcome to Python.org",
        "url": "https://www.python.org/",
        "snippet": "The official home of the Python Programming Language",
    }]


def test_old_database_gets_new_column(tmp_path):
    async def go():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
        async with engine.begin() as conn:
            await conn.execute(text(
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY, title VARCHAR, description VARCHAR, "
                "priority VARCHAR, completed BOOLEAN, due_date DATETIME, created_at DATETIME)"
            ))
        await init_db(engine)
        async with engine.connect() as conn:
            cols = [r[1] for r in (await conn.execute(text("PRAGMA table_info(tasks)"))).all()]
        await engine.dispose()
        return cols

    assert "completed_at" in asyncio.run(go())
