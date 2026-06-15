import json
import asyncio
from loguru import logger
from langchain_core.runnables import RunnableConfig

from app.state.agent_state import AgentState


async def dispatcher_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    调度 Agent — 系统唯一入口。
    负责：意图识别 → 任务分发。
    当前使用 LLM (qwen-turbo) 进行意图识别。
    """
    user_message = state["messages"][-1].content if state["messages"] else ""

    # 意图识别（调用 LLM）→ intent + format
    intent, output_format = await _detect_intent(user_message, state)

    # 模糊提问检测（"能干什么"等兜底引导）
    fuzzy_keywords = ["能干什么", "能做什么", "有什么功能", "你能做什么", "你会什么",
                      "有什么能力", "介绍一下", "你能干嘛", "试试", "test"]
    is_fuzzy = any(kw in user_message for kw in fuzzy_keywords)

    chat_id = config["configurable"]["chat_id"]
    logger.info(f"[调度] intent={intent}, chat_id={chat_id}")

    result: dict = {"intent": intent}
    if output_format:
        result["generate_format"] = output_format
    if is_fuzzy:
        result["is_fuzzy_intent"] = True

    return result


async def _detect_intent(message: str, state: AgentState) -> tuple[str, str | None]:
    """调用 LLM 进行意图识别 + 格式检测，返回 (intent, format)。"""
    from app.factory.llm_factory import create_llm
    from app.config import settings

    llm = create_llm(settings.INTENT_MODEL, tags=["skip_stream"])

    prompt = (
        "你是一个意图识别助手。根据用户输入，按以下优先级判定意图（命中即终止）：\n"
        "- report: 数据查询、报表生成、统计分析（最高优先）\n"
        "- travel: 旅游规划、行程安排、景点推荐\n"
        "- chat: 纯闲聊、日常问候、无任何实质任务\n"
        "- assistant: 除以上三类之外的所有请求（知识问答、文案撰写、事务协助等）\n\n"
        "输出格式（仅当用户明确要求「生成/导出/下载」文件时才填）：pdf / docx / xlsx / md / html\n"
        "重要：用户说「不生成」「不需要文档」「只要文本」等否定词时，格式必须填 none\n"
        "如用户未指定或仅提及文档但不要求生成，输出 none\n\n"
        f"用户输入：{message}\n\n"
        "请只回复两个单词：intent format（如 assistant none 或 report pdf）"
    )

    resp = await llm.ainvoke(prompt)
    parts = resp.content.strip().lower().split()
    intent = parts[0] if parts else "chat"
    if intent not in ("chat", "report", "travel", "assistant"):
        intent = "chat"
    output_format = parts[1] if len(parts) > 1 and parts[1] != "none" else None

    # 兜底：用户明确说不生成文档 → 强制 format=None
    neg_keywords = ["不生成", "不需要文档", "不用文档", "不导出", "不要文件", "只要文本", "纯文本"]
    if output_format and any(kw in message for kw in neg_keywords):
        output_format = None

    return intent, output_format
