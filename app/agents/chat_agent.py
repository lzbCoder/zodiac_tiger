from langchain_core.runnables import RunnableConfig

from app.state.agent_state import AgentState


def _build_memory_context(state: AgentState) -> str:
    parts = []

    summary = state.get("summary", "")
    if summary:
        parts.append(f"## 对话历史摘要\n{summary}")

    recalled = state.get("recalled_memories", "")
    if recalled:
        parts.append(f"## 用户长期记忆\n{recalled}")

    if not parts:
        return ""
    return "\n\n".join(parts) + "\n\n---\n"


async def chat_agent_node(state: AgentState, config: RunnableConfig) -> dict:
    """智能聊天节点：调用 LLM 生成回复，可选联网搜索。"""
    from app.factory.llm_factory import create_llm
    from app.config import settings

    user_message = state["messages"][-1].content if state["messages"] else ""
    enable_search = config["configurable"].get("enable_search", False)

    llm = create_llm(settings.CHAT_MODEL)

    history = state.get("messages", [])
    context = "\n".join(
        f"{'用户' if m.type == 'human' else 'AI'}: {m.content}"
        for m in history[-10:]
    )
    memory_ctx = _build_memory_context(state)
    prompt = (
        "你是越群山智能生活助手，一个专业的 AI 助手。请根据对话历史和用户长期记忆回答用户问题。\n\n"
        f"{memory_ctx}"
        f"对话历史：\n{context}\n\n"
        f"用户最新问题：{user_message}\n\n"
        "请用中文回答，专业、友好、简洁。"
    )

    if enable_search:
        from app.tools.executor import run_with_tools
        prompt += "\n（请使用 web_search 搜索用户问题，搜索结果中的信息优先级高于对话历史。即使对话历史中有不同信息，也必须以搜索结果为准。）"
        reply = await run_with_tools(llm, prompt)
    else:
        resp = await llm.ainvoke(prompt)
        reply = resp.content

    fmt = state.get("generate_format", "")
    is_physical = fmt and fmt not in ("none", "md")
    result: dict = {
        "generate_content": reply,
        "generate_format": fmt,
    }
    if not is_physical:
        result["messages"] = [{"role": "ai", "content": reply}]
    return result
