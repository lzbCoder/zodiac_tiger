from typing import NotRequired
from langchain_core.messages import AnyMessage
from app.state.agent_state import AgentState


class AssistantState(AgentState):
    """
    综合助手子图专用 State，继承 AgentState 全部字段。

    用于原生 Function Calling 的 ReAct 循环执行通用问答、文案撰写、知识咨询等综合任务。
    """
    task: NotRequired[str]
    """当前需要处理的用户任务描述。"""

    complexity: NotRequired[str]
    """任务复杂度判定：simple（直接执行）| complex（先规划再分步执行）。"""

    scratchpad: NotRequired[list[AnyMessage]]
    """ReAct 草稿消息通道（Human/AI(tool_calls)/Tool），每轮任务由 collect_task 重置。
    与 durable messages（仅存用户提问+最终AI回复）分离，避免工具噪音污染对话记忆。"""

    react_loop_count: int = 0
    """ReAct 思考-行动循环的迭代次数计数器。"""

    activated_tool_names: NotRequired[list[str]]
    """工具路由节点选出的工具名；工具管理节点会追加；planner 据此筛选 bind_tools。"""

    tool_search_exhausted: bool = False
    """工具管理在池中找不到所缺工具时置 True，防止"缺能力→管理→仍缺"死循环。"""

    final_answer: NotRequired[str]
    """生成的最终回答内容。"""

    skill_context: NotRequired[str]
    """已激活技能的 XML 上下文，由 activate_skill_node 写入，planner 读取；多技能时在节点内合并。"""

    activated_skill_keys: NotRequired[list[str]]
    """已激活的技能 key 列表。"""

    _activated_skill_infos: NotRequired[list[dict]]
    """激活技能的展示信息（display_name + skill_desc），仅用于 SSE 事件 detail，不影响业务路由。"""
