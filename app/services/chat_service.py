import json
import uuid
from sqlalchemy import select, func, delete

from app.db.session import get_db_session
from app.models.chat_history import ChatHistory
from app.models.execution_log import ExecutionLog
from app.models.execution_error_log import ExecutionErrorLog


async def create_session() -> str:
    return uuid.uuid4().hex


async def delete_session(session_id: str) -> None:
    # 先删产物（依赖 tasks 反查），再删任务，最后删对话/日志
    from app.services import artifact_service, task_service
    await artifact_service.delete_by_session(session_id)
    await task_service.delete_by_session(session_id)
    async with get_db_session() as session:
        await session.execute(delete(ChatHistory).where(ChatHistory.session_id == session_id))
        await session.execute(delete(ExecutionLog).where(ExecutionLog.session_id == session_id))
        await session.execute(delete(ExecutionErrorLog).where(ExecutionErrorLog.session_id == session_id))
        await session.commit()


async def list_sessions() -> list[dict]:
    async with get_db_session() as session:
        stmt = (
            select(
                ChatHistory.session_id,
                func.max(ChatHistory.create_time).label("last_time"),
            )
            .group_by(ChatHistory.session_id)
            .order_by(func.max(ChatHistory.create_time).desc())
            .limit(50)
        )
        result = await session.execute(stmt)
        rows = result.all()

        items = []
        for row in rows:
            title_stmt = (
                select(ChatHistory.content)
                .where(ChatHistory.session_id == row.session_id)
                .order_by(ChatHistory.create_time.asc())
                .limit(1)
            )
            title_result = await session.execute(title_stmt)
            title = title_result.scalar() or "新会话"
            items.append({
                "session_id": row.session_id,
                "title": title[:30],
                "last_time": row.last_time.strftime("%m-%d %H:%M") if row.last_time else "",
                "create_time": row.last_time.strftime("%Y-%m-%d %H:%M:%S") if row.last_time else "",
            })
    return items


async def save_message(session_id: str, role: str, content: str, chat_id: str | None = None) -> None:
    async with get_db_session() as session:
        obj = ChatHistory(
            session_id=session_id,
            role=role,
            content=content,
            chat_id=chat_id,
        )
        session.add(obj)
        await session.commit()


async def get_history(session_id: str, limit: int = 100) -> list[dict]:
    async with get_db_session() as session:
        stmt = (
            select(
                ChatHistory.id,
                ChatHistory.session_id,
                ChatHistory.role,
                ChatHistory.content,
                ChatHistory.chat_id,
                ChatHistory.create_time,
            )
            .where(ChatHistory.session_id == session_id)
            .order_by(ChatHistory.create_time.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.all()

    results = []
    for r in rows:
        exec_events = []
        if r.chat_id:
            from app.services import execution_log_service as els
            exec_events = await els.get_events_by_chat(r.chat_id) if r.chat_id else []
        results.append({
            "id": r.id,
            "session_id": r.session_id,
            "role": r.role,
            "content": r.content,
            "chat_id": r.chat_id,
            "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
            "steps": [],
            "execution_events": exec_events,
        })
    return results
