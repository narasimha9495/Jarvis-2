"""WebSocket endpoint for the real-time dashboard chat.

Client → server messages:
    {"type": "chat", "content": "...", "provider": "gemini" | null}
    {"type": "confirm", "confirm_id": "...", "approved": true | false}
    {"type": "ping"}

Server → client messages (all have "type" and "content"):
    welcome | chat | action | confirm | error | reminder | pong
"""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.config import get_settings
from app.core.agent import AgentReply, Conversation, handle_confirmation, handle_user_message
from app.core.llm_router import get_llm_router
from app.core.notifications import manager
from app.core.safety import get_safety_engine
from app.core.security import origin_allowed
from app.db import database
from app.models.schemas import WebSocketIncoming

logger = logging.getLogger(__name__)

ws_router = APIRouter()


def _out(reply: AgentReply) -> str:
    payload = {"type": reply.type, "content": reply.content}
    if reply.action:
        payload["action"] = reply.action
    if reply.confirm_id:
        payload["confirm_id"] = reply.confirm_id
    if reply.result is not None:
        payload["result"] = reply.result
    return json.dumps(payload, default=str)


@ws_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Refuse connections opened by other websites (cross-site WebSocket hijacking)
    if not origin_allowed(websocket.headers.get("origin"), websocket.headers.get("host")):
        logger.warning(f"Rejected WebSocket from origin {websocket.headers.get('origin')}")
        await websocket.close(code=1008)
        return

    await websocket.accept()
    manager.add(websocket)

    router = get_llm_router()
    conv = Conversation(max_messages=get_settings().max_history_messages)

    await websocket.send_text(json.dumps({
        "type": "welcome",
        "content": "Hello! I am Jarvis. How can I help you today?",
        "providers": router.get_available_providers(),
        "default_provider": router.default_provider_name,
    }))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = WebSocketIncoming.model_validate_json(raw)
            except ValidationError as e:
                await websocket.send_text(_out(AgentReply("error", f"Bad message: {e.errors()[0]['msg']}")))
                continue

            if msg.type == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "content": ""}))
                continue

            # One short-lived DB session per message
            async with database.async_session_maker() as db:
                if msg.type == "chat":
                    reply = await handle_user_message(
                        conv, msg.content, router=router, safety=get_safety_engine(),
                        db=db, provider=msg.provider,
                    )
                else:  # confirm
                    reply = await handle_confirmation(conv, msg.confirm_id, bool(msg.approved), db=db)

            await websocket.send_text(_out(reply))

    except WebSocketDisconnect:
        logger.info("Client disconnected.")
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        manager.remove(websocket)
