import uuid
import json
import asyncio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger

from app.schemas.chat import ChatRequest, ResumeRequest, AbortRequest
from app.services import chat_service
from app.services.summary_service import summarize_and_prune, get_latest_summary
from app.services import execution_log_service
from app.agents.graph import get_agent_graph
from app.state.agent_state import AgentState
from app.sse.event_sse import parse_events, AgentEvent
from app.config import settings
from app.db.redis import get_redis
from app.db.checkpoint import delete_checkpoint_thread
from app.utils.response import success, fail

router = APIRouter(tags=["聊天对话"])


# ========== 公共私有函数（/chat/stream 和 /chat/resume 共用） ==========

def _extract_ai_content(messages: list) -> str:
    """从 LangGraph messages 列表中提取最后一条 AI 回复文本。"""
    for m in reversed(messages):
        if hasattr(m, "type") and m.type == "ai":
            return m.content or ""
        if isinstance(m, dict) and m.get("role") == "ai":
            return m.get("content", "")
    return ""


def _make_interrupt_event(snap) -> AgentEvent | None:
    """检查 StateSnapshot 是否有待处理中断，有则构造 AgentEvent。"""
    if not snap or not snap.interrupts:
        return None
    for intr in snap.interrupts:
        try:
            ir = intr.value if hasattr(intr, 'value') else str(intr)
            return AgentEvent(
                event_type="interrupt", name="travel_params", status="running",
                content=json.dumps(ir, ensure_ascii=False) if isinstance(ir, dict) else str(ir),
            )
        except Exception:
            pass
    return None


async def _yield_fallback(snap, full_content, result: list):
    """兜底：从 state 提取 AI 逐字符发送。结果存入 result[0] 供调用方读取。"""
    if full_content or not snap or not snap.values:
        result.append(full_content)
        return
    ai = _extract_ai_content(snap.values.get("messages", []))
    if ai:
        result.append(ai)
        for char in ai:
            yield AgentEvent(event_type="token", name="reply", status="completed", content=char).to_sse()
            await asyncio.sleep(0.01)
    else:
        result.append(full_content)


def _yield_progress(name: str, buf: list):
    """yield 一对 running + completed 进度事件，同时追加到 buf 供后续入库。

    开始处理/处理完成 属于主阶段(stage)，标注 node_kind 供前端按大图标渲染。
    """
    for st in ("running", "completed"):
        ev = AgentEvent(event_type="progress", name=name, status=st,
                        metadata={"node_kind": "stage"})
        buf.append(ev)
        yield ev.to_sse()


async def _stream_graph(graph, input_data, config, events_buf, session_id: str = "",
                        content_out: list | None = None):
    """
    执行 LangGraph astream_events 并逐事件 yield SSE。
    流式 token 实时累积后通过 content_out[0] 传回，不再写入 events_buf。
    每 10 个事件检查一次 Redis abort 信号，收到则提前终止。
    在任何退出路径上（包括 GeneratorExit）都会关闭 LangGraph 流以释放 checkpoint 连接。

    入库规则：
    - thinking_token：瞬态增量，不入库（回显由 thinking(completed) 承载）
    - token：AI 完整回复已在 chat_history，不入库
    """
    full = ""
    r = None
    if session_id:
        try:
            r = await get_redis()
        except Exception:
            pass
    stream = graph.astream_events(input_data, config, version="v2")
    _check = 0
    try:
        async for ev in parse_events(stream):
            _check += 1
            if r and _check % 10 == 0:
                try:
                    if await r.get(f"abort:{session_id}"):
                        await r.delete(f"abort:{session_id}")
                        break
                except Exception:
                    pass
            if ev.event_type not in ("thinking_token", "token"):
                events_buf.append(ev)
            yield ev.to_sse()
            if ev.event_type == "token":
                full += ev.content
    except GeneratorExit:
        # 捕获 GeneratorExit，显式关闭 LangGraph 流以释放 checkpoint 连接池
        await asyncio.shield(stream.aclose())
        raise
    finally:
        if content_out is not None:
            content_out.append(full)


async def _finalize(snap, full_content, session_id, user_id, config, events_buf):
    """对话收尾：保存 AI 消息 + 触发摘要 + 异步写入执行日志。"""
    logger.info(f"[_finalize] session={session_id} full_content_len={len(full_content)} events_cnt={len(events_buf)} snap_is_none={snap is None}")
    if full_content:
        await chat_service.save_message(session_id, "ai", full_content, config["configurable"]["chat_id"])
    elif events_buf:
        # 有 pending interrupt（旅游参数收集等正常中断）时不保存，避免产生多余的"已终止"消息
        has_pending_interrupt = ((snap is not None and bool(getattr(snap, "interrupts", None))) or
                                 any(e.event_type == "interrupt" for e in events_buf))
        if not has_pending_interrupt:
            # 流被真正中断（手动终止等）时保存骨架，使 execution_events 能被正确回显
            await chat_service.save_message(session_id, "ai", "（任务已被手动终止）", config["configurable"]["chat_id"])
    await summarize_and_prune(
        snap.values if snap else {}, user_id, session_id, config)
    await execution_log_service.batch_save_events(
        events_buf, session_id, config["configurable"]["chat_id"])


def _build_stream_response(generator_fn):
    """统一构建 text/event-stream 响应，附带标准 headers。"""
    return StreamingResponse(generator_fn(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


# ========== /chat/stream：新建对话 ==========

"""聊天对话 API：/chat/stream 新建对话流 + /chat/resume 中断恢复流。"""

@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """核心对话接口：接收用户消息 → LangGraph 处理 → SSE 流式返回。"""

    async def event_generator():
        chat_id = uuid.uuid4().hex                    # 本轮对话唯一 ID
        session_id = req.session_id
        events_buf: list[AgentEvent] = []              # 累积所有事件，用于入 execution_log
        full_content = ""
        snap = None
        config = None
        graph = None
        user_id = "admin"
        stream_gen = None                              # 指向 _stream_graph 生成器，用于异常时关闭

        try:
            # 1. 保存用户消息到 chat_history
            await chat_service.save_message(session_id, "user", req.message, chat_id)

            # 2. 构建运行时 config（注入 thread_id、enable_search 等）
            config = {
                "configurable": {
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "session_id": session_id,
                    "thread_id": f"admin:{session_id}",
                    "enable_search": req.enable_search,
                }
            }

            # 3. 初始化 Agent 状态（含最新摘要）
            latest_summary = await get_latest_summary(user_id, session_id)
            initial_state: AgentState = {
                "messages": [{"role": "user", "content": req.message}],
                "intent": "chat", "summary": latest_summary,
            }

            graph = get_agent_graph()

            # 4. 开始处理进度
            for ev in _yield_progress("开始处理", events_buf):
                yield ev

            # 5. 执行 Graph 事件流
            content_out: list[str] = []
            stream_gen = _stream_graph(graph, initial_state, config, events_buf, session_id, content_out)
            async for _sse in stream_gen:
                yield _sse
            stream_gen = None
            full_content = content_out[0] if content_out else ""

            # 6. 检查中断（旅游子图缺参数等）
            snap = await graph.aget_state(config)
            interrupt_ev = _make_interrupt_event(snap)
            if interrupt_ev:
                events_buf.append(interrupt_ev)
                yield interrupt_ev.to_sse()

            # 7. 兜底 token 输出
            fb_result: list[str] = []
            async for fb_ev in _yield_fallback(snap, full_content, fb_result):
                yield fb_ev
            full_content = fb_result[0] if fb_result else full_content

            # 8. 处理完成（仅当无中断时）
            if not interrupt_ev:
                for ev in _yield_progress("处理完成", events_buf):
                    yield ev

            # 9. 结果事件 + 流结束
            yield AgentEvent(event_type="result", name="chat", status="completed",
                             content=full_content, metadata={
                                 "chat_id": chat_id,
                                 "session_title": req.message[:30],
                             }).to_sse()
            yield AgentEvent(event_type="done", name="stream", status="completed").to_sse()

        except GeneratorExit:
            # 先关闭 LangGraph 流 → 释放 checkpoint 连接 → 再落盘执行记录
            if stream_gen is not None:
                await asyncio.shield(stream_gen.aclose())
                stream_gen = None
            raise

        except Exception as e:
            logger.opt(exception=True).error(f"聊天处理异常: {e}")
            try:
                yield AgentEvent(event_type="error", name="exception", status="error", content=str(e)).to_sse()
            except GeneratorExit:
                raise

        finally:
            # 确保执行记录入库（即使客户端提前断开连接）
            # 注意：此处不再调用 graph.aget_state() —— 避免在 GeneratorExit 清理路径上复用 checkpointer 连接池中仍有 pending 查询的连接。
            # snap 保持 None 时 summarize_and_prune 自动跳过，不影响执行记录落盘。
            if config is not None:
                # 精准标记 terminated：只对没有对应 completed/error 事件的 running 事件转换，
                # 避免正常完成的节点产生 running→terminated + completed 双记录的数据混乱。
                from collections import defaultdict
                completed_positions: dict = defaultdict(list)
                for i, ev in enumerate(events_buf):
                    if ev.status in ('completed', 'error'):
                        completed_positions[(ev.event_type, ev.name)].append(i)
                for i, ev in enumerate(events_buf):
                    if ev.status == 'running':
                        key = (ev.event_type, ev.name)
                        if not any(pos > i for pos in completed_positions[key]):
                            ev.status = 'terminated'
                try:
                    await asyncio.shield(_finalize(snap, full_content, session_id, user_id, config, events_buf))
                except BaseException as e:
                    logger.error(f"保存执行记录失败: {e}")

    return _build_stream_response(event_generator)


# ========== /chat/resume：中断恢复 ==========

@router.post("/chat/resume")
async def chat_resume(req: ResumeRequest):
    """中断恢复接口：前端填充参数后调用，继续执行被 interrupt() 暂停的 Graph。"""
    from langgraph.types import Command

    async def resume_generator():
        events_buf: list[AgentEvent] = []
        full_content = ""
        snap = None
        graph = None
        full_config = None
        sid = ""
        stream_gen = None

        try:
            # 1. 从前端传的 thread_id 反推 session_id
            tid = req.config.get("configurable", {}).get("thread_id", "")
            sid = tid.replace("admin:", "") if tid.startswith("admin:") else uuid.uuid4().hex

            graph = get_agent_graph()

            # 2. chat_id：前端携带（保持与 /chat/stream 一致）
            cid = req.chat_id or uuid.uuid4().hex

            # 3. 构建完整 config（补 session_id / chat_id / user_id 等必填项）
            full_config = {
                "configurable": {
                    **req.config.get("configurable", {}),
                    "session_id": sid, "chat_id": cid, "user_id": "admin",
                }
            }

            # 4. 以 Command(resume=params) 恢复 Graph 执行
            content_out: list[str] = []
            stream_gen = _stream_graph(graph, Command(resume=req.params), full_config, events_buf, sid, content_out)
            async for _sse in stream_gen:
                yield _sse
            stream_gen = None
            full_content = content_out[0] if content_out else ""

            # 5. 检查是否还有后续中断
            snap = await graph.aget_state(full_config)
            interrupt_ev = _make_interrupt_event(snap)
            if interrupt_ev:
                events_buf.append(interrupt_ev)
                yield interrupt_ev.to_sse()

            # 6. 兜底 token
            fb_result: list[str] = []
            async for fb_ev in _yield_fallback(snap, full_content, fb_result):
                yield fb_ev
            full_content = fb_result[0] if fb_result else full_content

            # 7. 处理完成
            if not interrupt_ev:
                for ev in _yield_progress("处理完成", events_buf):
                    yield ev

            # 8. 结果 + 结束
            yield AgentEvent(event_type="result", name="resume", status="completed",
                             content=full_content).to_sse()
            yield AgentEvent(event_type="done", name="stream", status="completed").to_sse()

        except GeneratorExit:
            if stream_gen is not None:
                await asyncio.shield(stream_gen.aclose())
                stream_gen = None
            raise

        except Exception as e:
            logger.opt(exception=True).error(f"恢复执行失败: {e}")
            try:
                yield AgentEvent(event_type="error", name="exception", status="error", content=str(e)).to_sse()
            except GeneratorExit:
                raise

        finally:
            # 确保执行记录入库（即使客户端提前断开连接）
            # 注意：此处不再调用 graph.aget_state() —— 避免在 GeneratorExit 清理路径上
            # 复用 checkpointer 连接池中仍有 pending 查询的连接。
            if full_config is not None:
                # 精准标记 terminated：只对没有对应 completed/error 事件的 running 事件转换
                from collections import defaultdict
                completed_positions: dict = defaultdict(list)
                for i, ev in enumerate(events_buf):
                    if ev.status in ('completed', 'error'):
                        completed_positions[(ev.event_type, ev.name)].append(i)
                for i, ev in enumerate(events_buf):
                    if ev.status == 'running':
                        key = (ev.event_type, ev.name)
                        if not any(pos > i for pos in completed_positions[key]):
                            ev.status = 'terminated'
                try:
                    await asyncio.shield(_finalize(snap, full_content, sid, "admin", full_config, events_buf))
                except BaseException as e:
                    logger.error(f"保存执行记录失败: {e}")

    return _build_stream_response(resume_generator)


# ========== /chat/abort：终止执行流 ==========

@router.post("/chat/abort")
async def abort_chat(req: AbortRequest):
    """向 Redis 写入终止信号，_stream_graph 轮询到后中断当前执行流。"""
    try:
        r = await get_redis()
        await r.setex(f"abort:{req.session_id}", 30, "1")
        return success(message="终止信号已发送")
    except Exception as e:
        logger.error(f"终止信号发送失败: {e}")
        return fail(message=str(e))


# ========== 其他路由 ==========

@router.get("/chat/execution/{chat_id}")
async def get_execution_log(chat_id: str):
    """按 chat_id 查询执行日志，供前端回显。"""
    try:
        events = await execution_log_service.get_events_by_chat(chat_id)
        return success(events)
    except Exception as e:
        logger.error(f"获取执行日志失败: {e}")
        return fail(message=str(e))


@router.get("/chat/history")
async def chat_history(session_id: str):
    """会话历史：包含 execution_events 供回显。"""
    try:
        data = await chat_service.get_history(session_id)
        return success(data)
    except Exception as e:
        logger.error(f"获取对话历史失败: {e}")
        return fail(message=str(e))


@router.get("/chat/session/list")
async def session_list():
    """会话列表。"""
    try:
        data = await chat_service.list_sessions()
        return success(data)
    except Exception as e:
        logger.error(f"获取会话列表失败: {e}")
        return fail(message=str(e))


@router.post("/chat/session/new")
async def new_session():
    """创建新会话。"""
    try:
        session_id = await chat_service.create_session()
        return success({"session_id": session_id})
    except Exception as e:
        logger.error(f"创建会话失败: {e}")
        return fail(message=str(e))


@router.delete("/chat/session")
async def delete_session(session_id: str):
    """删除会话（含 chat_history、execution_log、checkpoint、Redis）。"""
    try:
        await chat_service.delete_session(session_id)
        r = await get_redis()
        await r.delete(f"session:{session_id}")
        thread_id = f"{settings.DEFAULT_USER_ID}:{session_id}"
        await delete_checkpoint_thread(thread_id)
        return success(message="会话已删除")
    except Exception as e:
        logger.error(f"删除会话失败: {e}")
        return fail(message=str(e))
