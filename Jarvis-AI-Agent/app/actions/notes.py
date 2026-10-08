"""Note management action."""

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.base import BaseAction
from app.db.models import Note


def note_to_dict(n: Note) -> dict[str, Any]:
    return {"id": n.id, "title": n.title, "content": n.content, "tags": n.tags}


class NoteAction(BaseAction):
    """Create, search and delete quick notes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def name(self) -> str:
        return "notes"

    @property
    def description(self) -> str:
        return "Create and manage quick notes"

    async def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        action = params.get("action")
        if not action:
            return {"success": False, "message": "Missing 'action' parameter."}

        handlers = {
            "create_note": self._create_note,
            "list_notes": self._list_notes,
            "search_notes": self._search_notes,
            "delete_note": self._delete_note,
        }
        handler = handlers.get(action)
        if not handler:
            return {"success": False, "message": f"Unknown action: {action}"}
        try:
            return await handler(params)
        except Exception as e:
            await self.session.rollback()
            return {"success": False, "message": f"Error executing note action: {e}"}

    async def _create_note(self, params: dict[str, Any]) -> dict[str, Any]:
        title = (params.get("title") or "").strip()
        if not title:
            return {"success": False, "message": "Title is required to create a note."}

        tags = params.get("tags") or ""
        if isinstance(tags, list):  # LLMs often send ["a", "b"]
            tags = ",".join(str(t) for t in tags)

        note = Note(title=title, content=params.get("content") or "", tags=tags)
        self.session.add(note)
        await self.session.commit()
        await self.session.refresh(note)

        return {"success": True, "message": f"Note #{note.id} created: {title}", "note_id": note.id}

    async def _list_notes(self, params: dict[str, Any]) -> dict[str, Any]:
        stmt = select(Note).order_by(Note.updated_at.desc())
        tag = params.get("tag")
        if tag:
            stmt = stmt.where(Note.tags.ilike(f"%{tag}%"))

        notes = (await self.session.execute(stmt)).scalars().all()
        return {"success": True, "message": f"Found {len(notes)} notes.", "notes": [note_to_dict(n) for n in notes]}

    async def _search_notes(self, params: dict[str, Any]) -> dict[str, Any]:
        # Accept both "keyword" and "query" — LLMs use either
        keyword = params.get("keyword") or params.get("query")
        if not keyword:
            return {"success": False, "message": "keyword is required for searching."}

        stmt = select(Note).where(
            or_(
                Note.title.ilike(f"%{keyword}%"),
                Note.content.ilike(f"%{keyword}%"),
                Note.tags.ilike(f"%{keyword}%"),
            )
        )
        notes = (await self.session.execute(stmt)).scalars().all()
        return {
            "success": True,
            "message": f"Found {len(notes)} notes matching '{keyword}'.",
            "notes": [note_to_dict(n) for n in notes],
        }

    async def _delete_note(self, params: dict[str, Any]) -> dict[str, Any]:
        note_id = params.get("note_id")
        if not note_id:
            return {"success": False, "message": "note_id is required."}

        note = await self.session.get(Note, int(note_id))
        if not note:
            return {"success": False, "message": f"Note {note_id} not found."}

        title = note.title
        await self.session.delete(note)
        await self.session.commit()
        return {"success": True, "message": f"Note #{note_id} '{title}' deleted."}
