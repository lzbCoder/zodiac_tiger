import time

from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.state.agent_state import AgentState
from app.services import memory_service
from app.config import settings


def _relative_time(timestamp_ms: int) -> str:
    if not timestamp_ms:
        return "未知时间"
    diff_days = (int(time.time() * 1000) - timestamp_ms) // (24 * 3600 * 1000)
    if diff_days == 0:
        return "今天"
    if diff_days == 1:
        return "昨天"
    if diff_days < 30:
        return f"{diff_days} 天前"
    if diff_days < 365:
        return f"{diff_days // 30} 个月前"
    return f"{diff_days // 365} 年前"


def _format_memories(memories: list[dict], profile: list[dict]) -> str:
    parts: list[str] = []

    if profile:
        lines = ["## 用户画像"]
        for p in profile:
            value = p.get("value", {})
            content = value.get("content", "") if isinstance(value, dict) else str(value)
            lines.append(f"- {p['key'].replace('pref:', '')}: {content}")
        parts.append("\n".join(lines))

    if memories:
        lines = ["## 相关记忆"]
        for m in memories:
            when = _relative_time(m.get("timestamp", 0))
            lines.append(
                f"- [{m['memory_type']}] {m['content']} (重要度:{m['importance']:.1f}, {when})"
            )
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


async def memory_recall_node(state: AgentState, config: RunnableConfig) -> dict:
    user_id = config["configurable"].get("user_id", settings.DEFAULT_USER_ID)
    user_message = state["messages"][-1].content if state["messages"] else ""

    try:
        memories = await memory_service.recall_memories(
            user_id, user_message, limit=settings.MEMORY_RECALL_LIMIT
        )
        profile = await memory_service.get_user_profile(user_id)
        recalled_text = _format_memories(memories, profile)
    except Exception as e:
        logger.warning(f"记忆召回失败: {e}")
        recalled_text = ""

    if recalled_text:
        logger.info(f"记忆召回: {len(memories)} 条记忆, {len(profile)} 条画像")

    return {
        "recalled_memories": recalled_text,
    }
