from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.state.agent_state import AgentState
from app.services import memory_service
from app.config import settings


def _last_user_message(messages: list) -> str | None:
    for m in reversed(messages):
        if hasattr(m, "type") and m.type == "human":
            return m.content
        if isinstance(m, dict) and m.get("role") == "user":
            return m["content"]
    return None


def _last_ai_message(messages: list) -> str | None:
    for m in reversed(messages):
        if hasattr(m, "type") and m.type == "ai":
            return m.content
        if isinstance(m, dict) and m.get("role") == "ai":
            return m["content"]
    return None


async def memory_extraction_node(state: AgentState, config: RunnableConfig) -> dict:
    user_id = config["configurable"].get("user_id", settings.DEFAULT_USER_ID)
    session_id = config["configurable"]["session_id"]

    user_msg = _last_user_message(state["messages"])
    ai_msg = _last_ai_message(state["messages"])

    if not user_msg or not ai_msg:
        return {"extracted_count": 0, "extracted_detail": ""}

    try:
        count, summaries = await memory_service.extract_and_save(user_id, session_id, user_msg, ai_msg)
    except Exception as e:
        logger.warning(f"记忆提取失败: {e}")
        count = 0
        summaries = []

    detail = f"提取了 {count} 条长期记忆"
    if summaries:
        detail += "\n" + "\n".join(f"• {s}" for s in summaries)

    return {"extracted_count": count, "extracted_detail": detail}
