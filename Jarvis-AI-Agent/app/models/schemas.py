"""Pydantic schemas for API request/response models."""

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from app.utils import from_db_datetime, utc_now

# Datetimes read from the DB are naive UTC; this marks them as UTC so the
# JSON says "...+00:00" and the browser converts to the user's local time.
UTCDateTime = Annotated[datetime, AfterValidator(from_db_datetime)]

Priority = Literal["low", "medium", "high"]


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatMessage(BaseModel):
    """A single chat message."""
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=utc_now)


class ChatRequest(BaseModel):
    """Request body for POST /api/chat."""
    message: str = Field(min_length=1, max_length=4000)
    provider: str | None = None


class ChatResponse(BaseModel):
    """Response body for POST /api/chat."""
    type: str  # chat | action | confirm | error
    response: str
    provider: str
    action_taken: str | None = None
    result: dict | None = None


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    priority: Priority = "medium"
    due_date: datetime | None = None  # without an offset = local time


class TaskResponse(BaseModel):
    id: int
    title: str
    description: str
    priority: str
    completed: bool
    due_date: UTCDateTime | None
    created_at: UTCDateTime
    completed_at: UTCDateTime | None = None

    model_config = {"from_attributes": True}


class ReminderCreate(BaseModel):
    message: str = Field(min_length=1, max_length=500)
    remind_at: datetime  # without an offset = local time


class ReminderResponse(BaseModel):
    id: int
    message: str
    remind_at: UTCDateTime
    triggered: bool
    created_at: UTCDateTime

    model_config = {"from_attributes": True}


class NoteCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str
    tags: str = ""  # comma-separated


class NoteResponse(BaseModel):
    id: int
    title: str
    content: str
    tags: str
    created_at: UTCDateTime
    updated_at: UTCDateTime

    model_config = {"from_attributes": True}


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = ""
    available_providers: list[str] = []
    default_provider: str | None = None
    currency_symbol: str = "₹"


class ProviderSwitch(BaseModel):
    provider: str


class WebSocketIncoming(BaseModel):
    """Messages the dashboard sends over the WebSocket."""
    type: Literal["chat", "confirm", "ping"]
    content: str = ""
    provider: str | None = None
    confirm_id: str | None = None
    approved: bool = False


class WebSocketMessage(BaseModel):
    """Generic server → client message (kept for API docs/compatibility)."""
    type: str  # welcome | chat | action | confirm | error | reminder
    content: str
    action: str | None = None
    confirm_id: str | None = None
    result: dict | None = None


class ExpenseCreate(BaseModel):
    amount: float = Field(gt=0)
    category: str = "general"
    description: str = ""
    date: str | None = None  # YYYY-MM-DD, defaults to today


class ExpenseResponse(BaseModel):
    id: int
    amount: float
    category: str
    description: str
    date: str
    created_at: UTCDateTime

    model_config = {"from_attributes": True}


class HabitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    frequency: str = "daily"


class HabitResponse(BaseModel):
    id: int
    name: str
    frequency: str
    active: bool
    created_at: UTCDateTime

    model_config = {"from_attributes": True}
