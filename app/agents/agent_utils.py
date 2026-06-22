"""Agent 子图共享工具函数"""

from langchain_core.tools import BaseTool
from langchain_core.messages import AIMessage, BaseMessage


async def astream_tool_call(llm_with_tools, messages: list[BaseMessage]) -> AIMessage:
    """流式调用已 bind_tools 的 LLM 并累积成完整 AIMessage。

    原生 Function Calling 用：astream 逐块累加（AIMessageChunk 相加）后得到带
    .content（推理文本，供思考流）与 .tool_calls（结构化决策）的完整 AIMessage。
    """
    acc = None
    async for chunk in llm_with_tools.astream(messages):
        acc = chunk if acc is None else acc + chunk
    if acc is None:
        return AIMessage(content="")
    # 累积得到的是 AIMessageChunk，转成普通 AIMessage 便于入 scratchpad
    return AIMessage(
        content=acc.content,
        tool_calls=getattr(acc, "tool_calls", []) or [],
        additional_kwargs=getattr(acc, "additional_kwargs", {}) or {},
    )


async def astream_accumulate(llm, prompt) -> str:
    """流式调用 LLM 并累积完整文本。

    用于"思考"型节点：必须走 astream 路径才能触发 on_chat_model_stream 事件
    （ainvoke 走非流式 _agenerate，不会发流式 token），但节点本身仍需整段文本解析 JSON。
    """
    content = ""
    async for chunk in llm.astream(prompt):
        if chunk.content:
            content += chunk.content
    return content


def tool_schema_text(tool: BaseTool) -> str:
    """从 LangChain BaseTool 的 args_schema 提取参数描述，内联到 prompt 中。"""
    schema_cls = getattr(tool, "args_schema", None)
    if schema_cls is None:
        return "无参数"
    try:
        js = schema_cls.model_json_schema()
        props: dict = js.get("properties") or {}
        if not props:
            return "无参数"
        required: set = set(js.get("required") or [])
        parts = [
            f"{k}({'必填' if k in required else '可选'}, {v.get('type', 'string')}): {v.get('description', '')}"
            for k, v in props.items()
        ]
        return "; ".join(parts)
    except Exception:
        return "无参数"


def build_tool_desc_section(tools: dict[str, BaseTool]) -> str:
    """将工具字典渲染为 prompt 中的"可用工具"段落。"""
    if not tools:
        return "\n可用工具：（无）"
    lines = "\n".join(
        f"- {name}：{t.description}\n  参数：{tool_schema_text(t)}"
        for name, t in tools.items()
    )
    return f"\n可用工具：\n{lines}"
