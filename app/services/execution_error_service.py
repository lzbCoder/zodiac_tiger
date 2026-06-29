import traceback
from datetime import datetime

from loguru import logger
from sqlalchemy import select, update

from app.config import settings
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
        "id": r.id,
        "session_id": r.session_id,
        "chat_id": r.chat_id,
        "error_node_name": r.error_node_name,
        "error_node_display_name": r.error_node_display_name,
        "exception_type": r.exception_type,
        "exception_info": r.exception_info,
        "exception_stack": r.exception_stack,
        "ai_diagnosis": r.ai_diagnosis,
        "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
    } for r in rows]


def _strip_code_fence(text: str) -> str:
    """剥离 LLM 可能包裹的 ```markdown / ``` 代码块标记，返回纯正文。"""
    text = text.strip()
    if text.startswith("```"):
        # 去掉首行 ```xxx
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


async def diagnose_error(error_id: int) -> str:
    """对指定错误行调用 LLM 进行诊断，结果写回该行并返回 markdown 文本。

    重复调用即覆盖上一次诊断结果（ai_diagnosis / diagnosis_time）。
    """
    from app.factory.llm_factory import create_llm
    from app.prompts.loader import render

    async with get_db_session() as session:
        row = await session.get(ExecutionErrorLog, error_id)
        if row is None:
            raise ValueError(f"错误记录不存在: id={error_id}")
        node_name = row.error_node_display_name or row.error_node_name or "未知节点"
        exception_type = row.exception_type or "未知异常"
        exception_info = row.exception_info or ""
        exception_stack = row.exception_stack or ""

    prompt = render(
        "error_diagnose",
        node_name=node_name,
        exception_type=exception_type,
        exception_info=exception_info,
        exception_stack=exception_stack,
    )
    llm = create_llm(settings.CHAT_MODEL, streaming=False)
    resp = await llm.ainvoke(prompt)
    diagnosis = _strip_code_fence(resp.content or "")

    async with get_db_session() as session:
        await session.execute(
            update(ExecutionErrorLog)
            .where(ExecutionErrorLog.id == error_id)
            .values(ai_diagnosis=diagnosis, diagnosis_time=datetime.now())
        )
        await session.commit()
    logger.info(f"AI 诊断完成并入库: error_id={error_id}, 长度={len(diagnosis)}")
    return diagnosis
