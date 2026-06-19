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
        item = {
            "event_type": r.event_type,
            "name": r.name,
            "status": r.status,
            "cost_ms": r.cost_ms,
            "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
        }
        # content 为 JSON 时，正文取 text，其余 metadata 字段整体并入
        # （node_kind / attach_to / react_round / tool_name / tool_args / tool_result / cost_sec / detail / intent ...）
        try:
            if content.startswith("{"):
                data = json.loads(content)
                item["content"] = data.get("text", "")
                item.update({k: v for k, v in data.items() if k != "text"})
            else:
                item["content"] = content
        except (json.JSONDecodeError, TypeError):
            item["content"] = content
        items.append(item)
    return items
