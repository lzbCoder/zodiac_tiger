from typing import NotRequired
from app.state.agent_state import AgentState


class ReportState(AgentState):
    """
    数据分析子图专用 State，继承 AgentState 全部字段。

    用于 ReAct 循环执行数据分析任务，收集工具调用结果并生成最终报告。
    """
    task: NotRequired[str]
    """当前需要执行的数据分析任务描述。"""

    current_thought: NotRequired[str]
    """ReAct 循环中当前的思考内容。"""

    current_action: NotRequired[str]
    """Planner 决策的工具名称，供 tool_executor 直接读取。"""

    current_action_input: NotRequired[dict]
    """Planner 决策的工具参数，供 tool_executor 直接读取。"""

    observations: NotRequired[list[dict]]
    """从工具调用中收集到的观察结果列表。"""

    tool_calls: NotRequired[list[dict]]
    """已执行的工具调用记录列表。"""

    react_loop_count: int = 0
    """ReAct 思考-行动循环的迭代次数计数器。"""

    is_finish: bool = False
    """标识数据分析任务是否已完成。"""

    observation_result: NotRequired[str]
    """最终的观察结果汇总字符串。"""

    final_report: NotRequired[str]
    """生成的最终数据分析报告内容。"""

