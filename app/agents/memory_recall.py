from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.state.agent_state import AgentState
from app.services import memory_service
from app.config import settings


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
            lines.append(f"- [{m['memory_type']}] {m['content']} (重要度:{m['importance']:.1f})")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


async def memory_recall_node(state: AgentState, config: RunnableConfig) -> dict:
    user_id = config["configurable"].get("user_id", settings.DEFAULT_USER_ID)
    user_message = state["messages"][-1].content if state["messages"] else ""

    try:
        memories = await memory_service.recall_memories(user_id, user_message, limit=5)
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
