import json
import uuid
from datetime import datetime
from sqlalchemy import select, func, delete, update

from app.db.session import get_db_session
from app.models.chat_history import ChatHistory
from app.models.chat_session import ChatSession
from app.models.execution_log import ExecutionLog
from app.models.execution_error_log import ExecutionErrorLog


async def create_session(user_id: str = "admin") -> str:
    """新建会话：在 chat_session 主表落一行，title 暂为空（首条消息时写入）。"""
    session_id = uuid.uuid4().hex
    async with get_db_session() as session:
        session.add(ChatSession(session_id=session_id, user_id=user_id))
        await session.commit()
    return session_id


async def rename_session(session_id: str, title: str) -> None:
    """重命名会话：覆盖 chat_session.title。"""
    async with get_db_session() as session:
        await session.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(title=title[:100])
        )
        await session.commit()


async def set_session_pinned(session_id: str, pinned: bool) -> None:
    """置顶/取消置顶：pinned_at 置顶时记当前时间，取消时清空。"""
    async with get_db_session() as session:
        await session.execute(
            update(ChatSession)
            .where(ChatSession.session_id == session_id)
            .values(pinned=1 if pinned else 0,
                    pinned_at=datetime.now() if pinned else None)
        )
        await session.commit()


async def delete_session(session_id: str) -> None:
    """删除会话的权威数据（Tier 1）：产物、任务、对话、执行日志、会话主表
    全部在【同一个 PostgreSQL 事务】内完成，单次 commit。
    任一步失败则整体回滚，调用方据此报错——保证不会留下半删的中间态。
    Redis / checkpoint 等派生数据的清理属 Tier 2，由调用方做 best-effort 处理，不在本事务内。"""
    from app.services import artifact_service, task_service
    async with get_db_session() as session:
        # 先删产物（依赖 tasks 反查），再删任务，最后删对话/日志/会话主表
        await artifact_service.delete_by_session_in_tx(session, session_id)
        await task_service.delete_by_session_in_tx(session, session_id)
        await session.execute(delete(ChatHistory).where(ChatHistory.session_id == session_id))
        await session.execute(delete(ExecutionLog).where(ExecutionLog.session_id == session_id))
        await session.execute(delete(ExecutionErrorLog).where(ExecutionErrorLog.session_id == session_id))
        await session.execute(delete(ChatSession).where(ChatSession.session_id == session_id))
        await session.commit()


async def list_sessions() -> list[dict]:
    """以 chat_session 主表为准，LEFT JOIN chat_history 取最后活跃时间。

    排序：置顶优先 → 置顶时间倒序 → 最后活跃时间（无消息回退创建时间）倒序。
    """
    async with get_db_session() as session:
        last_sub = (
            select(
                ChatHistory.session_id.label("sid"),
                func.max(ChatHistory.create_time).label("last_time"),
            )
            .group_by(ChatHistory.session_id)
            .subquery()
        )
        order_time = func.coalesce(last_sub.c.last_time, ChatSession.create_time)
        stmt = (
            select(
                ChatSession.session_id,
                ChatSession.title,
                ChatSession.pinned,
                ChatSession.create_time,
                last_sub.c.last_time,
            )
            .select_from(ChatSession)
            .outerjoin(last_sub, last_sub.c.sid == ChatSession.session_id)
            .order_by(
                ChatSession.pinned.desc(),
                ChatSession.pinned_at.desc().nullslast(),
                order_time.desc(),
            )
            .limit(50)
        )
        result = await session.execute(stmt)
        rows = result.all()

    items = []
    for row in rows:
        active = row.last_time or row.create_time
        items.append({
            "session_id": row.session_id,
            "title": (row.title or "新会话")[:30],
            "pinned": int(row.pinned or 0),
            "last_time": active.strftime("%m-%d %H:%M") if active else "",
            "create_time": active.strftime("%Y-%m-%d %H:%M:%S") if active else "",
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
        # 首条用户消息自动作为标题（仅 title 为空时写入，重命名后不再覆盖）
        if role == "user":
            await session.execute(
                update(ChatSession)
                .where(ChatSession.session_id == session_id, ChatSession.title.is_(None))
                .values(title=content[:100])
            )
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
