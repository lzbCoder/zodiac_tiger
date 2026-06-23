"""元工具：不实际执行，仅作为 ReAct planner 的"信号"。"""

from langchain_core.tools import tool


@tool
def request_tools(capability: str) -> str:
    """当现有工具无法满足任务、需要额外能力时调用本工具，并用 capability 描述所缺能力。

    例如："需要查询数据库的能力""需要生成折线图的能力"。
    不要用它执行具体任务，它只用于申请补充工具。
    """
    # 仅作信号，真正处理在工具管理节点；正常不会被执行。
    return ""
