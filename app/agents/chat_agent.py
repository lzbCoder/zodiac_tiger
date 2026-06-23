from langchain_core.runnables import RunnableConfig

from app.state.agent_state import AgentState
from app.prompts.loader import render, render_messages


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

    return render("chat_fuzzy", configs=configs)


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
            "messages": [{"role": "ai", "content": reply}],
        }
        return result

    llm = create_llm(settings.CHAT_MODEL, streaming=True)

    memory_ctx = _build_memory_context(state)

    if enable_search:
        # 联网搜索路径：run_with_tools 走原生 FC（字符串入参），单独组装精简 prompt
        from app.tools.executor import run_with_tools
        search_prompt = (
            f"{memory_ctx}用户问题：{user_message}\n"
            "（请使用 web_search 搜索用户问题，搜索结果中的信息优先级高于对话历史。"
            "即使对话历史中有不同信息，也必须以搜索结果为准。）"
        )
        reply = await run_with_tools(llm, search_prompt)
    else:
        # 普通路径：角色化消息 = SystemMessage(角色+记忆) + 近 N 条历史（已是 Human/AI 类型）
        messages = render_messages("chat_reply", memory_ctx=memory_ctx)
        messages += list(state.get("messages", []))[-10:]
        reply = ""
        async for chunk in llm.astream(messages):
            if chunk.content:
                reply += chunk.content

    fmt = state.get("generate_format", "")
    is_physical = fmt and fmt not in ("none", "md")
    result: dict = {
        "generate_content": reply,
    }
    if not is_physical:
        result["messages"] = [{"role": "ai", "content": reply}]
    return result
