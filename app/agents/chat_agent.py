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


async def _build_fuzzy_prompt(state: AgentState) -> str | None:
    """模糊提问时，从数据库读取意图展示配置，组装结构化能力介绍 prompt。"""
    if not state.get("is_fuzzy_intent"):
        return None

    from app.services.intent_display_service import get_display_list

    configs = await get_display_list()
    if not configs:
        return None

    lines = ["你是越群山智能生活助手。用户正在询问你能做什么，请根据以下能力配置生成友好的结构化自我介绍。\n"]
    lines.append("你的核心能力：\n")
    for c in configs:
        if not c.get("enable"):
            continue
        lines.append(f"{c['show_name']}")
        lines.append(f"{c['intent_desc']}")
        lines.append(f"示例提问：{c['demo_input']}")
        lines.append("")

    lines.append("请用热情、清晰的语言介绍自己，直接展示以上能力。格式参考：")
    lines.append("""
你好！我是越群山智能助手，目前支持以下核心能力：

1. 🗺️ 智能旅游规划
{能力介绍}
示例：{示例话术}

2. 📊 智能数据分析报表
{能力介绍}
示例：{示例话术}

3. 💬 通用智能问答
{能力介绍}
示例：{示例话术}

你可以直接修改示例提问发送给我，快速体验对应功能！
""")
    return "\n".join(lines)


async def chat_agent_node(state: AgentState, config: RunnableConfig) -> dict:
    """智能聊天节点：调用 LLM 生成回复，可选联网搜索。"""
    from app.factory.llm_factory import create_llm
    from app.config import settings

    user_message = state["messages"][-1].content if state["messages"] else ""
    enable_search = config["configurable"].get("enable_search", False)

    # 模糊提问 → 直接读数据库组装能力引导
    fuzzy_prompt = await _build_fuzzy_prompt(state)
    if fuzzy_prompt:
        llm = create_llm(settings.CHAT_MODEL)
        resp = await llm.ainvoke(fuzzy_prompt)
        reply = resp.content
        result: dict = {
            "generate_content": reply,
            "generate_format": state.get("generate_format", ""),
            "messages": [{"role": "ai", "content": reply}],
        }
        return result

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
