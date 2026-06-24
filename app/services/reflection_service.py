"""反思服务：从任务执行过程（execution_log）复盘提炼可复用规则，写入程序记忆。

后台异步触发（chat._finalize 后），不阻塞响应，不进图。触发时机：
  (a) 每 N 轮交互；
  (b) 重大事件（任务完成 / 生成重要产物）—— 由调用方传 major_event=True；
  (c) 反馈通道：用户显式纠正（"以后别用X"）→ 直接落 pitfall 规则。
"""
import json

from sqlalchemy import select, func
from loguru import logger

from app.db.session import get_db_session
from app.models.chat_history import ChatHistory
from app.services import procedural_service, execution_log_service, task_service
from app.prompts.loader import render
from app.config import settings

# 反馈通道关键词（用户显式纠正）
_FEEDBACK_KEYWORDS = ["以后别用", "以后不要用", "别再用", "不要再用", "下次别", "以后不要", "别用"]


async def _user_turn_count(session_id: str) -> int:
    async with get_db_session() as session:
        return (await session.execute(
            select(func.count()).select_from(ChatHistory)
            .where(ChatHistory.session_id == session_id, ChatHistory.role == "user")
        )).scalar() or 0


def _build_tool_sequence(events: list[dict]) -> tuple[str, bool]:
    """从执行事件提炼工具序列文本 + 是否报错。"""
    lines: list[str] = []
    had_error = False
    for ev in events:
        if ev.get("status") == "error" or ev.get("event_type") == "error":
            had_error = True
        if ev.get("event_type") == "tool":
            name = ev.get("tool_name") or ev.get("name") or "tool"
            args = (ev.get("tool_args") or "")[:120]
            result = (ev.get("tool_result") or ev.get("content") or "")[:160]
            lines.append(f"- 工具 {name}({args}) → {result}")
    return ("\n".join(lines) if lines else "（本轮无工具调用）"), had_error


async def _reflect_from_log(user_id: str, session_id: str, chat_id: str) -> None:
    """复盘 execution_log → LLM 提炼规则 → 去重合并入库。"""
    from app.factory.llm_factory import create_llm

    events = await execution_log_service.get_events_by_chat(chat_id)
    if not events:
        return
    tool_seq, had_error = _build_tool_sequence(events)

    task = await task_service.get_active_task(session_id)
    task_title = task.title if task else "（未知任务）"
    task_type = task.task_type if task else "assistant"

    llm = create_llm(settings.MEMORY_SUMMARY_MODEL)
    prompt = render("procedural_reflect", task_title=task_title, task_type=task_type,
                    had_error="是" if had_error else "否", tool_sequence=tool_seq)
    resp = await llm.ainvoke(prompt)
    text = resp.content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"[反思] LLM 返回非 JSON: {resp.content[:160]}")
        return

    count = 0
    for item in items or []:
        content = (item.get("content") or "").strip()
        if not content:
            continue
        await procedural_service.save_rule(
            user_id=user_id, content=content,
            memory_type=item.get("type", "rule"),
            source_task_type=task_type, success=not had_error,
        )
        count += 1
    if count:
        logger.info(f"[反思] 沉淀 {count} 条程序记忆 (session={session_id})")


async def maybe_reflect(user_id: str, session_id: str, chat_id: str,
                        user_message: str, major_event: bool = False) -> None:
    """后台反思入口（fire-and-forget 调用）。独立 try，任何失败不影响主流程。"""
    try:
        # (c) 反馈通道：显式纠正直接落 pitfall
        if any(kw in user_message for kw in _FEEDBACK_KEYWORDS):
            await procedural_service.save_rule(
                user_id=user_id, content=user_message.strip(),
                memory_type="pitfall", source_task_type=None, success=False,
            )
            logger.info(f"[反思] 用户反馈直接落规则 (session={session_id})")
            return

        # (a)/(b) 每 N 轮 或 重大事件
        turn = await _user_turn_count(session_id)
        if not major_event and (turn == 0 or turn % settings.REFLECTION_TURN_INTERVAL != 0):
            return
        await _reflect_from_log(user_id, session_id, chat_id)
    except Exception as e:
        logger.warning(f"[反思] maybe_reflect 失败: {e}")
