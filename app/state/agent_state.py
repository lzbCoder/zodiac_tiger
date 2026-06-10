from typing import NotRequired
from langgraph.graph.message import MessagesState


class AgentState(MessagesState):
    """
    Agent 执行状态。

    MessagesState 已提供:
        messages: Annotated[list[AnyMessage], add_messages]

    状态字段规则:
        - 需跨节点累积 → Annotated[T, reducer]
        - 被 router 读取 → 放 state (router 只能读 state)
        - 一次性快照 → 放 config.configurable (chat_id, session_id 等)
    """
    intent: NotRequired[str]
    """路由键。dispatcher 写入，route_by_intent 读取。"""

    summary: NotRequired[str]
    """对话摘要。后台异步生成，下次对话通过 摘要表中 恢复。"""

    recalled_memories: NotRequired[str]
    """召回记忆文本。memory_recall 写入，dispatcher 注入 LLM prompt。"""

    generate_content: NotRequired[str]
    """业务 Agent 生成的文本内容，供 document_agent 消费。"""

    generate_format: NotRequired[str]
    """目标文件格式 pdf/docx/xlsx/md，dispatcher 提取，业务 Agent 透传。"""

    charts: NotRequired[list[dict]]
    """图表 ECharts option 列表（由子图透传至前端渲染）。"""

    extracted_count: NotRequired[int]
    """本轮提取并保存的长期记忆条数。memory_extraction 写入，可通过 SSE 下发给前端。"""

    extracted_detail: NotRequired[str]
    """本轮提取记忆的摘要文本，如 '[preference] 喜欢咖啡'，供调试与前端展示。"""
