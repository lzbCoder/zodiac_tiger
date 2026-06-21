import traceback

from sqlalchemy import select

from app.db.session import get_db_session
from app.models.execution_error_log import ExecutionErrorLog


async def save_error(
    session_id: str,
    chat_id: str,
    node_name: str,
    exc: BaseException,
) -> None:
    """记录一条节点执行错误日志。节点中文展示名取自 event_sse.NODE_LABELS。"""
    from app.sse.event_sse import NODE_LABELS
    display_name = NODE_LABELS.get(node_name, node_name)
    stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    async with get_db_session() as session:
        session.add(ExecutionErrorLog(
            session_id=session_id or None,
            chat_id=chat_id or None,
            error_node_name=(node_name or "")[:100],
            error_node_display_name=(display_name or "")[:100],
            exception_type=exc.__class__.__name__[:100],
            exception_info=str(exc)[:1000],
            exception_stack=stack[:8000],
        ))
        await session.commit()


async def get_errors_by_chat(chat_id: str) -> list[dict]:
    """按 chat_id 查询错误日志。"""
    async with get_db_session() as session:
        stmt = (
            select(ExecutionErrorLog)
            .where(ExecutionErrorLog.chat_id == chat_id)
            .order_by(ExecutionErrorLog.id.asc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return [{
        "session_id": r.session_id,
        "chat_id": r.chat_id,
        "error_node_name": r.error_node_name,
        "error_node_display_name": r.error_node_display_name,
        "exception_type": r.exception_type,
        "exception_info": r.exception_info,
        "exception_stack": r.exception_stack,
        "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
    } for r in rows]
