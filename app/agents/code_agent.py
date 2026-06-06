from loguru import logger
from langchain_core.runnables import RunnableConfig

from app.state.agent_state import AgentState


async def code_agent_node(state: AgentState, config: RunnableConfig) -> dict:
    """
    代码生成 Agent。
    拆解用户需求 → 技术选型 → 逐文件生成代码 → 注释优化 → 项目结构生成 → 源码打包。
    """
    user_message = state["messages"][-1].content if state["messages"] else ""
    chat_id = config["configurable"]["chat_id"]

    from app.factory.llm_factory import create_llm
    from app.config import settings

    llm = create_llm(settings.CHAT_MODEL)

    enable_search = config["configurable"].get("enable_search", False)

    prompt = (
        "你是一个资深全栈工程师。请根据用户需求，生成相应的代码片段。\n"
        "要求(根据用户问题按需添加)：\n"
        "1. **需求分析**：拆解用户需求为功能模块\n"
        "2. **技术选型**：推荐合适的技术栈并说明理由\n"
        "3. **代码实现**：编写代码，包含必要的注释\n"
        "4. **使用说明**：如何运行\n\n"
        f"用户需求：{user_message}\n\n"
        "请用 Markdown 格式输出，代码块使用对应的语言标记。"
    )

    if enable_search:
        from app.tools.executor import run_with_tools
        prompt += "\n（请使用 web_search 搜索用户问题，搜索结果中的信息优先级高于对话历史。即使对话历史中有不同信息，也必须以搜索结果为准。）"
        resp_content = await run_with_tools(llm, prompt)
    else:
        resp = await llm.ainvoke(prompt)
        resp_content = resp.content

    logger.info(f"[代码Agent] 任务 {chat_id} 完成")
    fmt = state.get("generate_format", "")
    is_physical = fmt and fmt not in ("none", "md")

    result: dict = {
        "generate_content": resp_content,
        "generate_format": fmt,
    }
    if not is_physical:
        result["messages"] = [{"role": "ai", "content": resp_content}]
    return result
