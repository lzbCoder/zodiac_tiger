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


    is_fuzzy_intent: NotRequired[bool]
    """模糊提问标记。dispatcher 检测到"能干什么"等模糊提问时写入 True，chat_agent 据此返回能力引导。"""

    active_task_id: NotRequired[str]
    """当前焦点任务 task_id。task_manager 写入，artifact_store 据此归属产物。"""

    task_action: NotRequired[str]
    """本轮任务动作：NEW_TASK / CONTINUE_TASK / SWITCH_TASK / ARTIFACT_OPERATION。task_manager 写入。"""

    active_task_type: NotRequired[str]
    """焦点任务类型 chat/travel/assistant。task_manager 写入；dispatcher 在 ARTIFACT_OPERATION 轮据此硬路由 intent，使"改产物"回到产出它的 agent。"""

    current_artifact_id: NotRequired[str]
    """当前任务最新产物 id。task_manager 读出、artifact_store 产生新版本后更新。"""

    last_file_id: NotRequired[str]
    """本轮 document_agent 生成的文件记录 id，供 artifact_store 关联产物文件。"""

    procedural_context: NotRequired[str]
    """命中的程序记忆（可复用规则）文本。activate_skill 召回写入，planner 与 skill_context 同位置注入。"""

    extracted_count: NotRequired[int]
    """本轮提取并保存的长期记忆条数。memory_extraction 写入，可通过 SSE 下发给前端。"""

    extracted_detail: NotRequired[str]
    """本轮提取记忆的摘要文本，如 '[preference] 喜欢咖啡'，供调试与前端展示。"""
