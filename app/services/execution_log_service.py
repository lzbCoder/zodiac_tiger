from sqlalchemy import select

from app.db.session import get_db_session
from app.models.execution_log import ExecutionLog


async def batch_save_events(
    events: list,
    session_id: str,
    chat_id: str,
) -> None:
    """异步批量写入执行日志。"""
    if not events:
        return
    import json
    async with get_db_session() as session:
        for ev in events:
            content = ev.content[:5000] if ev.content else ""
            meta = getattr(ev, "metadata", None) or {}
            if meta:
                content = json.dumps({"text": content, **meta}, ensure_ascii=False)
            session.add(ExecutionLog(
                session_id=session_id,
                chat_id=chat_id,
                event_type=ev.event_type,
                name=ev.name,
                status=ev.status,
                content=content,
                cost_ms=ev.cost_ms,
            ))
        await session.commit()


async def get_events_by_chat(chat_id: str) -> list[dict]:
    """按 chat_id 查询执行日志。"""
    import json
    async with get_db_session() as session:
        stmt = (
            select(ExecutionLog)
            .where(ExecutionLog.chat_id == chat_id)
            .order_by(ExecutionLog.id.asc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
    items = []
    for r in rows:
        content = r.content or ""
        intent = ""
        detail = ""
        parent_node = ""
        react_round = None
        tool_args = ""
        cost_sec = None
        try:
            if content.startswith("{"):
                data = json.loads(content)
                content = data.get("text", content)
                intent = data.get("intent", "")
                detail = data.get("detail", "")
                parent_node = data.get("parent_node", "")
                react_round = data.get("react_round")
                tool_args = data.get("tool_args", "")
                cost_sec = data.get("cost_sec")
        except (json.JSONDecodeError, TypeError):
            pass
        item = {
            "event_type": r.event_type,
            "name": r.name,
            "status": r.status,
            "content": content,
            "cost_ms": r.cost_ms,
            "intent": intent,
            "detail": detail,
            "parent_node": parent_node,
            "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
        }
        if react_round is not None:
            item["react_round"] = react_round
        if tool_args:
            item["tool_args"] = tool_args
        if cost_sec is not None:
            item["cost_sec"] = cost_sec
        items.append(item)
    return items
