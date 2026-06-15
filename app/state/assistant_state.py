from typing import NotRequired
from app.state.agent_state import AgentState


class AssistantState(AgentState):
    """
    综合助手子图专用 State，继承 AgentState 全部字段。

    用于 ReAct 循环执行通用问答、文案撰写、知识咨询等综合任务。
    """
    task: NotRequired[str]
    """当前需要处理的用户任务描述。"""

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
    """标识任务是否已完成。"""

    observation_result: NotRequired[str]
    """最近一次工具执行的结果字符串。"""

    final_answer: NotRequired[str]
    """生成的最终回答内容。"""
