import json
import asyncio
from loguru import logger
from langchain_core.runnables import RunnableConfig

from app.state.agent_state import AgentState
from app.prompts.loader import render


async def dispatcher_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    调度 Agent — 系统唯一入口。
    负责：意图识别 → 任务分发。
    当前使用 LLM (qwen-turbo) 进行意图识别。
    """

    user_message = state["messages"][-1].content if state["messages"] else ""

    # 意图识别（调用 LLM）→ intent + format
    intent, output_format = await _detect_intent(user_message, state)

    # ARTIFACT_OPERATION（改已有产物）→ 硬路由回产出它的 agent（focus task_type），
    # 修复"改行程参数重规划被误判 assistant"；仅覆盖 intent，format 仍按本轮检测。
    # 仅限"操作产物"这一窄动作，CONTINUE/SWITCH 不绑，保留"一任务多 agent"。
    if state.get("task_action") == "ARTIFACT_OPERATION":
        task_type = state.get("active_task_type", "")
        if task_type in ("chat", "travel", "assistant") and task_type != intent:
            logger.info(f"[调度] ARTIFACT_OPERATION 按 task_type 硬路由 intent: {intent} → {task_type}")
            intent = task_type

    # 模糊提问检测（"能干什么"等兜底引导）
    fuzzy_keywords = ["能干什么", "能做什么", "有什么功能", "你能做什么", "你会什么",
                      "有什么能力", "介绍一下", "你能干嘛", "试试", "test"]
    is_fuzzy = any(kw in user_message for kw in fuzzy_keywords)

    chat_id = config["configurable"]["chat_id"]
    logger.info(f"[调度] intent={intent}, format={output_format or 'none'}, chat_id={chat_id}")

    # 新建任务时回填 task_type（task_manager 建任务时留空，待意图识别后定型）
    if state.get("task_action") == "NEW_TASK" and state.get("active_task_id"):
        try:
            from app.services import task_service
            await task_service.set_task_type(state["active_task_id"], intent)
        except Exception as e:
            logger.warning(f"[调度] 回填 task_type 失败: {e}")

    # 每轮强制写入 generate_format：无格式时写 "none"，避免 checkpointer 跨轮残留旧值
    # 导致下游 route_by_format 读到上一轮格式而误入 document_agent。
    result: dict = {
        "intent": intent,
        "generate_format": output_format or "none",
    }
    if is_fuzzy:
        result["is_fuzzy_intent"] = True

    return result


async def _detect_intent(message: str, state: AgentState) -> tuple[str, str | None]:
    """调用 LLM 进行意图识别 + 格式检测，返回 (intent, format)。"""
    from app.factory.llm_factory import create_llm
    from app.config import settings

    llm = create_llm(settings.INTENT_MODEL, tags=["skip_stream"])

    prompt = render("dispatcher_intent", message=message)

    resp = await llm.ainvoke(prompt)
    parts = resp.content.strip().lower().split()
    intent = parts[0] if parts else "chat"
    if intent not in ("chat", "travel", "assistant"):
        intent = "chat"
    output_format = parts[1] if len(parts) > 1 and parts[1] != "none" else None

    # 兜底：用户明确说不生成文档 → 强制 format=None
    neg_keywords = ["不生成", "不需要文档", "不用文档", "不导出", "不要文件", "只要文本", "纯文本"]
    if output_format and any(kw in message for kw in neg_keywords):
        output_format = None

    return intent, output_format
