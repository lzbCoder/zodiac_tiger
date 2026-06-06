import json
from loguru import logger
from sqlalchemy import select, func

from app.config import settings
from app.db.session import get_db_session
from app.db.checkpoint import cleanup_checkpoints, cleanup_expired_blobs
from app.models.conversation_summary import ConversationSummary


SUMMARY_PROMPT = """你是一个会话压缩系统。

目标：
在尽量少 token 下，
保留未来对话需要的重要上下文。

保留：
- 用户目标
- 用户偏好
- 已完成步骤
- 当前计划
- 未解决问题
- 重要实体

删除：
- 寒暄
- 重复内容
- 无意义闲聊
- 临时工具输出

输出要求：
- 简洁
- 结构化
- 不超过300字

输出结构化摘要：
{
  "goal": "目标描述",
  "preferences": ["偏好1", "偏好2"],
  "completed": ["已完成事项"],
  "pending": ["待解决问题"]
}

请直接输出 JSON，不要包含 markdown 代码块标记。"""


async def _generate_summary(messages: list, existing_summary: str) -> str:
    from app.factory.llm_factory import create_llm

    llm = create_llm(settings.MEMORY_SUMMARY_MODEL, streaming=False)

    history = "\n".join(
        f"{'用户' if (hasattr(m, 'type') and m.type == 'human') else 'AI'}: {m.content}"
        for m in messages[-30:]
    )

    prompt = (
        f"{SUMMARY_PROMPT}\n\n"
        f"已有摘要（如有）：{existing_summary or '无'}\n\n"
        f"最近对话：\n{history}"
    )

    resp = await llm.ainvoke(prompt)
    text = resp.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _estimate_tokens(text: str) -> int:
    return len(text) // 2


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


TOKEN_THRESHOLD = 6000


def _estimate_messages_tokens(messages: list) -> int:
    """估算 messages 列表的总 token 数（中文约 1 token/字，英文约 1 token/4字符，取平均 ~字符/2）。"""
    total = 0
    for m in messages:
        content = getattr(m, "content", "") or ""
        total += len(content) // 2
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
        if total_tokens > TOKEN_THRESHOLD:
            existing_summary = state.get("summary", "")
            await _run_summarization(messages, existing_summary, user_id, session_id, config)

        # 2. checkpoint 管理：始终执行（与摘要独立）
        thread_id = config["configurable"]["thread_id"]
        await cleanup_checkpoints(thread_id)
        await cleanup_expired_blobs()

    except Exception as e:
        logger.warning(f"会话后处理失败: {e}")
