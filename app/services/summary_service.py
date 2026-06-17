import json
from loguru import logger
from sqlalchemy import select, func

from app.config import settings
from app.db.session import get_db_session
from app.db.checkpoint import cleanup_checkpoints, cleanup_expired_blobs
from app.models.conversation_summary import ConversationSummary
from app.prompts.loader import render


async def _generate_summary(messages: list, existing_summary: str) -> str:
    from app.factory.llm_factory import create_llm

    llm = create_llm(settings.MEMORY_SUMMARY_MODEL, streaming=False)

    history = "\n".join(
        f"{'用户' if (hasattr(m, 'type') and m.type == 'human') else 'AI'}: {m.content}"
        for m in messages[-30:]
    )

    prompt = render("summary_compress", existing_summary=existing_summary or "无", history=history)

    resp = await llm.ainvoke(prompt)
    text = resp.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _estimate_tokens(text: str) -> int:
    chinese = sum(1 for c in text if '一' <= c <= '鿿')
    return chinese + (len(text) - chinese) // 4


async def _get_max_version(session_id: str) -> int:
    async with get_db_session() as session:
        stmt = (
            select(func.coalesce(func.max(ConversationSummary.summary_version), 0))
            .where(ConversationSummary.session_id == session_id)
        )
        result = await session.execute(stmt)
        version = result.scalar() or 0
    return version


async def get_latest_summary(user_id: str, session_id: str) -> str:
    """查询用户在某 session 下的最新摘要，用于初始化 AgentState.summary。"""
    async with get_db_session() as session:
        stmt = (
            select(ConversationSummary.summary)
            .where(ConversationSummary.user_id == user_id)
            .where(ConversationSummary.session_id == session_id)
            .order_by(ConversationSummary.summary_version.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        summary = result.scalar()
    return summary or ""


async def _save_summary(
    user_id: str,
    session_id: str,
    summary: str,
    version: int,
    message_count: int,
) -> None:
    async with get_db_session() as session:
        obj = ConversationSummary(
            user_id=user_id,
            session_id=session_id,
            summary=summary,
            summary_version=version,
            message_count=message_count,
            token_estimate=_estimate_tokens(summary),
        )
        session.add(obj)
        await session.commit()


def _estimate_messages_tokens(messages: list) -> int:
    total = 0
    for m in messages:
        content = getattr(m, "content", "") or ""
        total += _estimate_tokens(content)
    return total


async def _run_summarization(
    messages: list,
    existing_summary: str,
    user_id: str,
    session_id: str,
    config: dict,
) -> None:
    """生成摘要 → 存 PG → 裁剪 messages → 更新 checkpoint。"""
    new_summary = await _generate_summary(messages, existing_summary)
    if not new_summary:
        return

    prev_version = await _get_max_version(session_id)
    version = prev_version + 1
    await _save_summary(user_id, session_id, new_summary, version, len(messages))
    logger.info(f"会话摘要已保存: session={session_id}, version={version}")

    trimmed = list(messages[-20:])

    from app.db.checkpoint import get_checkpoint_repo

    repo = await get_checkpoint_repo()
    await repo.update_state(config, {
        "messages": trimmed,
        "summary": new_summary,
    })
    logger.info(f"Checkpoint 状态已更新: session={session_id}, messages={len(trimmed)}")


async def summarize_and_prune(
    state: dict,
    user_id: str,
    session_id: str,
    config: dict,
) -> None:
    try:
        messages = state.get("messages", [])
        if not messages:
            return

        total_tokens = _estimate_messages_tokens(messages)

        # 1. 触发摘要的条件：总 token 超过阈值
        if total_tokens > settings.SUMMARY_TOKEN_THRESHOLD:
            existing_summary = state.get("summary", "")
            await _run_summarization(messages, existing_summary, user_id, session_id, config)

        # 2. checkpoint 管理：始终执行（与摘要独立）
        thread_id = config["configurable"]["thread_id"]
        await cleanup_checkpoints(thread_id)
        await cleanup_expired_blobs()

    except Exception as e:
        logger.warning(f"会话后处理失败: {e}")
