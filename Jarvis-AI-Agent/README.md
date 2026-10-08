# 🤖 Jarvis AI Agent

A modular, privacy-first personal productivity AI assistant built with **FastAPI**, **WebSocket**, and multi-provider LLM support.

> **What problem does this solve?**
> A single AI interface to manage your tasks, reminders, notes, expenses and habits — running on your own machine, asking before anything destructive, and working with multiple LLM providers (including fully offline via Ollama).

---

## ✨ Features

### Core
- **Multi-Provider LLM Support** — Google Gemini, OpenAI, and Ollama (local/offline); switch from the dashboard
- **Real-time Chat** — WebSocket conversation that remembers recent context
- **Safety Engine** — Every AI action is risk-checked; deletions need your confirmation; system commands are always blocked
- **Local-first** — Data in a local SQLite file, server listens on `127.0.0.1` by default, secrets in `.env`
- **Modern Dashboard** — Iron Man Jarvis-inspired dark UI with live sidebars

### Productivity
- **Task Management** — Create, list, complete/reopen, and delete tasks with priorities and due dates
- **Reminders** — Set reminders in plain language; they pop up in the dashboard (and as desktop notifications) when due
- **Quick Notes** — Create, search, and delete notes with tags
- **Pomodoro Timer** — Focus sessions with configurable work/break durations
- **Habit Tracker** — Daily habits with streaks and completion reports
- **Daily Summary** — Productivity report with a 0–100 score, plus weekly trends

### Utilities
- **Web Search** — DuckDuckGo (no API key needed)
- **Weather** — Current conditions + 3-day forecast via Open-Meteo (no API key needed)
- **Expense Tracker** — Log and categorize spending, summaries by day/week/month
- **System Monitor** — CPU, RAM, disk, battery and top processes

---

## 🏗️ Architecture

```
app/
├── main.py                 # FastAPI app, startup, security middleware
├── config.py               # Settings from .env (cached)
├── utils.py                # JSON extraction, time-zone helpers, formatting
├── api/
│   ├── routes.py           # REST endpoints (sidebar panels, /api/chat)
│   └── websocket.py        # Real-time chat + confirmations
├── core/
│   ├── agent.py            # ★ The agent: system prompt, action registry, replies
│   ├── llm_router.py       # Multi-provider routing (one shared router)
│   ├── safety.py           # Risk levels, blocked actions, rate limits
│   ├── security.py         # Origin checks (blocks other websites)
│   ├── notifications.py    # Open dashboard connections (for push)
│   ├── reminder_scheduler.py # Fires due reminders
│   ├── stt.py              # Speech-to-text (Whisper) — not wired in yet
│   └── tts.py              # Text-to-speech (pyttsx3) — not wired in yet
├── providers/
│   ├── base.py             # Provider interface (multi-turn chat)
│   ├── gemini_provider.py  # google-genai SDK
│   ├── openai_provider.py
│   └── ollama_provider.py
├── actions/                # One module per feature (tasks, notes, habits, ...)
├── db/
│   ├── database.py         # Async SQLite + small schema upgrades
│   └── models.py           # SQLAlchemy models
└── models/
    └── schemas.py          # Pydantic v2 schemas

frontend/
├── index.html              # Dashboard UI
└── js/app.js               # WebSocket client & UI logic

tests/                      # pytest suite (unit + end-to-end)
```

### How a message flows

```
You type → WebSocket → agent.py → LLM (with recent history)
                                   │
              plain text ◀─────────┤
                                   └─▶ JSON action → SafetyEngine
                                          blocked      → error
                                          dangerous    → confirm dialog (id kept on the server)
                                          safe/moderate→ action runs → readable result
```

The system prompt is **generated from the action registry** in `app/core/agent.py`, so the LLM is only ever told about actions that actually exist.

---

## 🚀 Quick Start (Windows PowerShell)

### 1. Clone & install

```powershell
git clone https://github.com/narasimha9495/Jarvis-AI-Agent.git
cd Jarvis-AI-Agent
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> If PowerShell refuses to run `Activate.ps1`, run once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### 2. Configure

```powershell
Copy-Item .env.example .env
notepad .env   # add at least one API key, or use Ollama
```

### 3. Run

```powershell
python -m app.main
# Open http://localhost:8000
```

<details>
<summary>macOS / Linux</summary>

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m app.main
```
</details>

---

## ⚙️ Configuration

All settings live in `.env` (see `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Google Gemini API key | — |
| `OPENAI_API_KEY` | OpenAI API key | — |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://localhost:11434` |
| `DEFAULT_LLM_PROVIDER` | `gemini`, `openai` or `ollama` | `gemini` (falls back if no key) |
| `GEMINI_MODEL` / `OPENAI_MODEL` / `OLLAMA_MODEL` | Model names | see `.env.example` |
| `APP_HOST` | Network interface | `127.0.0.1` (this computer only) |
| `APP_PORT` | Port | `8000` |
| `CURRENCY_SYMBOL` | Shown for expenses | `₹` |
| `MAX_HISTORY_MESSAGES` | Chat context sent to the LLM | `20` |
| `REMINDER_CHECK_SECONDS` | How often reminders are checked | `30` |

> **Offline mode:** set `DEFAULT_LLM_PROVIDER=ollama`, install [Ollama](https://ollama.com) and run `ollama pull llama3.2`. No API keys needed.

> **Model retirement:** providers retire models regularly. If you get "model not found" errors, update `GEMINI_MODEL` / `OPENAI_MODEL` in `.env` to a current model name.

---

## 🛡️ Safety & Security

Every action the AI proposes is classified before it runs:

| Risk Level | Examples | Behavior |
|------------|----------|----------|
| ✅ **Safe** | List tasks, search, weather, system status | Runs immediately |
| ⚠️ **Moderate** | Create task, set reminder, add expense | Runs immediately |
| 🔴 **Dangerous** | Delete task / note / expense / habit, any unknown action | Asks you to confirm first |
| 🚫 **Blocked** | Shell commands, file deletion, system changes | Always refused |

Other protections:
- **Confirmations can't be forged.** The server remembers what it asked you to confirm (by a random id). The browser only answers yes/no, so it can't swap in a different action.
- **Other websites are refused.** WebSocket connections and data-changing requests from other origins are rejected, so a malicious page you visit can't control Jarvis on `localhost`.
- **Local by default.** The server listens on `127.0.0.1`. There is no login, so only set `APP_HOST=0.0.0.0` if you understand that anyone on your network could use it.
- **No HTML injection.** Everything shown in the dashboard is escaped, including AI replies and note contents.
- **Rate limits** per action (e.g. max 5 deletions/minute).

The assistant **never** runs shell commands.

---

## 🧪 Running Tests

```powershell
pip install -r requirements-dev.txt
pytest tests -v
```

The suite includes end-to-end tests (`tests/test_integration.py`) that drive the WebSocket with a scripted fake LLM, so no API keys or network are needed.

---

## 🔌 LLM Providers

| Provider | API Key | Offline | Setup |
|----------|---------|---------|-------|
| **Gemini** | Yes | No | `GEMINI_API_KEY` in `.env` (uses the `google-genai` SDK) |
| **OpenAI** | Yes | No | `OPENAI_API_KEY` in `.env` |
| **Ollama** | No | Yes | Install Ollama, `ollama pull llama3.2` |

Switch providers from the dashboard dropdown or `POST /api/providers/switch`.

---

## 📡 API Endpoints

Full interactive docs at **`/docs`** (Swagger UI).

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Status, providers, currency |
| `GET` | `/api/providers` | Configured + reachable providers |
| `POST` | `/api/providers/switch` | Change default provider |
| `POST` | `/api/chat` | One-shot chat with the agent (no confirmations) |
| `GET/POST` | `/api/tasks` | List / create tasks |
| `PATCH` | `/api/tasks/{id}/complete` · `/reopen` | Mark done / not done |
| `DELETE` | `/api/tasks/{id}` | Delete a task |
| `GET/POST` | `/api/reminders` | List / create reminders |
| `POST` | `/api/reminders/{id}/dismiss` | Dismiss a reminder |
| `GET/POST` | `/api/notes` | List / create notes |
| `DELETE` | `/api/notes/{id}` | Delete a note |
| `GET` | `/api/weather/{city}` | Weather + forecast |
| `GET/POST` | `/api/expenses` | List / add expenses |
| `GET` | `/api/expenses/summary?period=` | Spending by category |
| `DELETE` | `/api/expenses/{id}` | Delete an expense |
| `GET/POST` | `/api/habits` | List / create habits |
| `POST` | `/api/habits/{id}/log` | Log a habit for today |
| `GET` | `/api/habits/report?period=` | Completion report |
| `DELETE` | `/api/habits/{id}` | Delete a habit |
| `POST` | `/api/pomodoro/start` | Start a focus session |
| `GET` | `/api/pomodoro/{id}` | Session status |
| `POST` | `/api/pomodoro/{id}/complete` | Complete a pomodoro |
| `DELETE` | `/api/pomodoro/{id}` | Stop a session |
| `GET` | `/api/system/status` · `/api/system/processes` | System health |
| `GET` | `/api/summary/daily` · `/api/summary/weekly` | Productivity reports |
| `WS` | `/ws` | Real-time chat |

---

## 🛠️ Tech Stack

- **Backend**: Python 3.11+, FastAPI, Uvicorn
- **Frontend**: Vanilla HTML/CSS/JS (no framework)
- **Database**: SQLite + SQLAlchemy 2 (async)
- **LLM**: google-genai, openai, ollama
- **Optional voice** (`requirements-voice.txt`, not wired in yet): Whisper, pyttsx3
- **Testing**: pytest + pytest-asyncio

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
