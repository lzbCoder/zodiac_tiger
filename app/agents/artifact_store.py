"""产物存储节点 — memory_extraction 之前的汇聚节点（轻量、无 LLM）。

仅把"重要成果"（生成的文件 + 报告/方案类长文本）落库为带版本树的 artifact，
归属当前焦点任务；闲聊等非重要成果直接透传。
"""
from loguru import logger
from langchain_core.runnables import RunnableConfig

from app.state.agent_state import AgentState
from app.services import task_service, artifact_service

_REPORT_MIN_LEN = 200   # 报告/方案类长文本阈值


def _is_real_file(fmt: str) -> bool:
    return bool(fmt) and fmt not in ("none", "md", "text")


def _is_significant(intent: str, fmt: str, content: str) -> bool:
    """重要成果判定：① 生成了实体文件；② 旅游方案；③ 任意 intent 的长文本（含闲聊）。

    chat 文本也入库（type=text），使其按 task 形成版本链 = 任务历史，
    让"纯聊天任务被切回继续/导出"也有源内容可加载。
    """
    if _is_real_file(fmt):
        return True
    if intent == "travel" and content:
        return True
    if len(content) >= _REPORT_MIN_LEN:
        return True
    return False


async def artifact_store_node(state: AgentState, config: RunnableConfig) -> dict:
    task_id = state.get("active_task_id", "")
    content = state.get("generate_content", "")
    fmt = (state.get("generate_format", "") or "").lower().strip()
    intent = state.get("intent", "chat")

    # 能力引导类回复（"能干什么"等）非真实成果 → 不落噪音版本
    if state.get("is_fuzzy_intent"):
        return {}

    # 无任务归属或非重要成果 → 透传，不写库
    if not task_id or not _is_significant(intent, fmt, content):
        return {}

    try:
        parent_id = state.get("current_artifact_id") or None
        artifact_type = fmt if _is_real_file(fmt) else "text"
        artifact = await artifact_service.add_artifact(
            task_id=task_id,
            artifact_type=artifact_type,
            content=content,
            content_summary=content[:200] if content else None,
            parent_artifact_id=parent_id,
            file_id=state.get("last_file_id") if _is_real_file(fmt) else None,
        )
        await task_service.set_current_artifact(task_id, artifact.artifact_id)
        chat_id = config["configurable"].get("chat_id", "")
        logger.info(f"[产物] task={task_id} 新增 v{artifact.version} ({artifact_type}), chat_id={chat_id}")
        return {"current_artifact_id": artifact.artifact_id}
    except Exception as e:
        logger.warning(f"[产物] artifact_store 失败，跳过: {e}")
        return {}
