"""任务上下文取数 —— 按"任务作用域"加载已有产物内容。

供两处使用：
- assistant 子图 bypass 导出节点：取最新产物原文直接渲染文档；
- ReAct planner：把任务已有进展注入提示，让助手感知上文。

核心约束：SWITCH_TASK 不得用"会话最近消息"兜底——最近消息属于"刚离开的任务"，
会造成跨任务串台。故消息兜底仅对 CONTINUE_TASK 开放，其余以产物（task 作用域）为锚。
"""
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.state.agent_state import AgentState
from app.services import artifact_service

_CONTEXT_MAX_CHARS = 4000   # 注入提示的内容截断上限，避免提示膨胀


def _last_ai_content(messages: list) -> str:
    """取消息列表中最后一条 AI 回复的文本（仅 CONTINUE_TASK 兜底用）。"""
    for m in reversed(messages or []):
        role = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else None)
        if role == "ai":
            return getattr(m, "content", None) or (m.get("content", "") if isinstance(m, dict) else "")
    return ""


async def load_task_source_content(state: AgentState, config: RunnableConfig) -> str | None:
    """返回"该回合应感知的那个任务"的最新原始内容；取不到返回 None。

    取数优先级：
      1) current_artifact_id → 该产物 content
      2) 退化 list_versions(active_task_id) 取最新一条 content
      3) 仅 CONTINUE_TASK：退化 state["messages"] 最后一条 AI 回复
    """
    task_action = state.get("task_action", "")
    artifact_id = state.get("current_artifact_id", "")
    task_id = state.get("active_task_id", "")

    try:
        if artifact_id:
            art = await artifact_service.get_artifact(artifact_id)
            if art and art.content:
                return art.content

        if task_id:
            versions = await artifact_service.list_versions(task_id)
            for art in reversed(versions):   # 版本升序 → 取最新
                if art.content:
                    return art.content
    except Exception as e:
        logger.warning(f"[任务上下文] 产物加载失败: {e}")

    if task_action == "CONTINUE_TASK":
        ai = _last_ai_content(state.get("messages", []))
        if ai:
            return ai

    return None


async def build_task_context(state: AgentState, config: RunnableConfig) -> str:
    """返回注入 ReAct planner 的格式化上下文块；NEW_TASK 或无内容时返回 ""。"""
    if state.get("task_action", "") == "NEW_TASK":
        return ""

    content = await load_task_source_content(state, config)
    if not content:
        return ""

    snippet = content[:_CONTEXT_MAX_CHARS]
    if len(content) > _CONTEXT_MAX_CHARS:
        snippet += "\n…（内容过长已截断）"
    return f"\n\n## 本任务已有进展（请在此基础上继续，不要重复已完成的工作）\n{snippet}"
