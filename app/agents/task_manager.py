"""任务管理节点 — 位于意图识别之前，维护长任务生命周期。

判断本轮动作（新建/继续/切换/产物操作），维护 tasks 表的焦点与状态，
但不替代 dispatcher：意图识别每轮照常进行。
"""
import json

from loguru import logger
from langchain_core.runnables import RunnableConfig

from app.state.agent_state import AgentState
from app.services import task_service
from app.prompts.loader import render

_VALID_ACTIONS = {"NEW_TASK", "CONTINUE_TASK", "SWITCH_TASK", "ARTIFACT_OPERATION"}


def _format_active(task) -> str:
    if not task:
        return "（无，当前没有进行中的任务）"
    return f"task_id={task.task_id} | 标题：{task.title} | 类型：{task.task_type or '未定'} | 状态：{task.status}"


def _format_recent(tasks: list) -> str:
    if not tasks:
        return "（无历史任务）"
    return "\n".join(
        f"{i+1}. task_id={t.task_id} | {t.title} | 状态：{t.status}"
        for i, t in enumerate(tasks)
    )


async def _classify(user_message: str, active_task, recent_tasks: list) -> tuple[str, str, bool]:
    """调用 LLM 判定动作，返回 (action, target_task_id, completed)。"""
    from app.factory.llm_factory import create_llm
    from app.config import settings

    llm = create_llm(settings.INTENT_MODEL, tags=["skip_stream"])
    prompt = render(
        "task_manager",
        user_message=user_message,
        active_task=_format_active(active_task),
        recent_tasks=_format_recent(recent_tasks),
    )
    resp = await llm.ainvoke(prompt)
    text = resp.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    data = json.loads(text)
    action = data.get("action", "NEW_TASK")
    if action not in _VALID_ACTIONS:
        action = "NEW_TASK"
    return action, (data.get("task_id") or "").strip(), bool(data.get("completed", False))


async def task_manager_node(state: AgentState, config: RunnableConfig) -> dict:
    user_message = state["messages"][-1].content if state["messages"] else ""
    session_id = config["configurable"]["session_id"]
    user_id = config["configurable"].get("user_id", "admin")

    try:
        active_task = await task_service.get_active_task(session_id)
        recent_tasks = await task_service.list_recent_tasks(session_id, limit=10)

        # 无任何历史任务 → 直接新建，省一次 LLM
        if not active_task and not recent_tasks:
            action, target_id, completed = "NEW_TASK", "", False
        else:
            action, target_id, completed = await _classify(user_message, active_task, recent_tasks)

        # 动作 → 焦点/状态流转
        if action == "SWITCH_TASK" and target_id:
            target = await task_service.get_task(target_id)
            if target:
                if target.status == "completed":
                    await task_service.reopen(target_id)
                else:
                    await task_service.touch(target_id)
                focus = target
            else:  # 目标不存在，退化为新建
                action = "NEW_TASK"
                focus = await task_service.create_task(session_id, user_id, user_message[:80])
        elif action == "NEW_TASK" or active_task is None:
            action = "NEW_TASK"
            focus = await task_service.create_task(session_id, user_id, user_message[:80])
        else:  # CONTINUE_TASK / ARTIFACT_OPERATION
            await task_service.touch(active_task.task_id)
            focus = active_task

        if completed and focus:
            await task_service.update_status(focus.task_id, "completed")

        chat_id = config["configurable"].get("chat_id", "")
        logger.info(f"[任务] action={action}, task_id={focus.task_id if focus else None}, chat_id={chat_id}")

        return {
            "active_task_id": focus.task_id if focus else "",
            "task_action": action,
            "current_artifact_id": (focus.current_artifact_id or "") if focus else "",
            "active_task_type": (focus.task_type or "") if focus else "",
        }
    except Exception as e:
        # 兜底：任务管理失败不阻断主流程
        logger.warning(f"[任务] task_manager 失败，降级无任务: {e}")
        return {"active_task_id": "", "task_action": "NEW_TASK",
                "current_artifact_id": "", "active_task_type": ""}
