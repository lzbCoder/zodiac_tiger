"""LangGraph 事件流 → AgentEvent → SSE 消息转换"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta


_CST = timezone(timedelta(hours=8))

INTENT_LABELS: dict[str, str] = {
    "chat": "聊天", "report": "报表", "travel": "旅游", "assistant": "助手",
}

NODE_LABELS: dict[str, str] = {
    # 主流程节点
    "memory_recall":              "记忆召回",
    "dispatcher":                 "意图识别",
    "report_agent":               "报表生成",
    "travel_agent":               "旅游规划",
    "assistant_agent":            "智能助手",
    "chat_agent":                 "对话聊天",
    "document_agent":             "文档生成",
    "memory_extraction":          "记忆提取",
    # 旅游规划子图-内部节点
    "collect_params":             "参数提取",
    "validate_params":            "参数校验",
    "query_geo":                  "地理编码",
    "query_weather":              "天气查询",
    "query_route":                "路线查询",
    "generate_plan":              "行程生成",
    # 数据分析子图-内部节点
    "collect_task":               "任务收集",
    "planner":                    "分析思考",
    "tool_executor":              "工具执行",
    "observation":                "数据观察",
    "report_generator":           "报告生成",
    # 综合助手子图-内部节点
    "assistant_collect_task":     "任务收集",
    "assistant_triage":           "任务分析",
    "assistant_planner":          "分析思考",
    "assistant_tool_executor":    "工具执行",
    "assistant_observation":      "数据观察",
    "assistant_answer_generator": "回答生成",
}

# 子图节点 → 父节点映射（用于 tool/children 归属）
SUB_NODE_PARENT: dict[str, str] = {
    # 旅游规划子图：子级 --> 父级
    "collect_params":             "旅游规划",
    "validate_params":            "旅游规划",
    "query_geo":                  "旅游规划",
    "query_weather":              "旅游规划",
    "query_route":                "旅游规划",
    "generate_plan":              "旅游规划",
    # 数据分析子图：子级 --> 父级
    "collect_task":               "报表生成",
    "planner":                    "报表生成",
    "tool_executor":              "报表生成",
    "observation":                "报表生成",
    "report_generator":           "报表生成",
    # 综合助手子图：子级 --> 父级
    "assistant_collect_task":     "智能助手",
    "assistant_triage":           "智能助手",
    "assistant_planner":          "智能助手",
    "assistant_tool_executor":    "智能助手",
    "assistant_observation":      "智能助手",
    "assistant_answer_generator": "智能助手",
}

# 流式输出结果的节点
STREAM_NODES = {"chat_agent", "document_agent", 
                "generate_plan", "report_generator", "assistant_answer_generator"}


def _now_ts() -> int:
    return int(datetime.now(_CST).timestamp() * 1000)


# ---- 统一事件模型 ----

@dataclass
class AgentEvent:
    event_type: str       # thought | tool | progress | token | retrieval
    name: str             # 步骤/工具/模型名
    status: str           # running | done | error
    content: str = ""
    cost_ms: int = 0
    timestamp: int = field(default_factory=_now_ts)
    metadata: dict = field(default_factory=dict)

    def to_sse(self) -> str:
        d = {
            "type": self.event_type,
            "name": self.name,
            "status": self.status,
            "content": self.content,
            "cost_ms": self.cost_ms,
            "timestamp": self.timestamp,
            **self.metadata,
        }
        return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


# ---- Node start tracking ----

_node_starts:  dict[str, int] = {}  # node_name → start_timestamp
_cycle_counts: dict[str, int] = {}  # ReAct 节点出现次数
_react_rounds: dict[str, int] = {}  # ReAct 节点当前轮次（start 时写入，end 时读取）

# ReAct 循环节点（每轮独立命名，不合并）
_REACT_NODES = {
    "planner", "tool_executor", "observation",
    "assistant_planner", "assistant_tool_executor", "assistant_observation",
}

# ReAct 进度事件应隐藏的节点（属于助手子图内部循环，对用户无信息价值）
_HIDE_PROGRESS = {
    "assistant_collect_task", "assistant_triage",
    "assistant_planner", "assistant_tool_executor", "assistant_observation",
}


# ---- parse_events 私有辅助函数 ----

def _get_label_parent(raw_parent: str) -> str:
    """将父节点原始名称转换为显示名，ReAct 节点附加当前轮次编号。"""
    if raw_parent in _REACT_NODES:
        return f"{NODE_LABELS[raw_parent]} #{_cycle_counts.get(raw_parent, 1)}"
    return NODE_LABELS.get(raw_parent, raw_parent)


def _handle_chain_start(_node: str, display_name: str, data: dict) -> list[AgentEvent]:
    """处理 on_chain_start：记录开始时间，返回需发射的进度/计划事件列表。"""
    events = []
    _node_starts[_node] = _now_ts()
    parent = SUB_NODE_PARENT.get(_node, "")
    meta_dict = {"parent_node": parent} if parent else {}

    # ReAct 节点：从 input 读取 react_loop_count，确保与 end 时一致
    if _node in _REACT_NODES:
        input_round = data.get("input", {}).get("react_loop_count", 0)
        react_round = input_round + 1 if _node == "planner" else input_round  # planner 自增 1，其余透传
        _react_rounds[_node] = react_round
        meta_dict["react_round"] = react_round

    # 助手子图内部循环节点隐藏进度事件
    if _node not in _HIDE_PROGRESS:
        events.append(AgentEvent(
            event_type="progress", name=display_name,
            status="running", content=_node,
            metadata=meta_dict,
        ))

    # planner 节点：预发射 plan 事件，将首个 pending 步骤置为 in_progress，供前端提前展示转圈动画
    if _node in ("assistant_planner", "planner"):
        _input = data.get("input", {}) or {}
        if isinstance(_input, dict):
            _steps = _input.get("plan_steps")
            if _steps:
                _updated = [dict(s) for s in _steps]
                for _s in _updated:
                    if _s.get("status") == "pending":
                        _s["status"] = "in_progress"
                        break
                events.append(AgentEvent(
                    event_type="plan", name="执行计划",
                    status="running",
                    metadata={"steps": _updated, "parent_node": "智能助手"},
                ))

    return events


def _build_chain_end_meta(name: str, output: dict) -> tuple[dict, int]:
    """计算节点耗时，构建 on_chain_end 的 meta_dict，含 intent/detail/react_round 等字段。"""
    start = _node_starts.pop(name, _now_ts())
    cost = _now_ts() - start

    raw_intent = output.get("intent", "")
    label_intent = INTENT_LABELS.get(raw_intent, raw_intent)
    meta_dict = {"intent": label_intent} if raw_intent and name == "dispatcher" else {}

    parent = SUB_NODE_PARENT.get(name, "")
    if parent:
        meta_dict["parent_node"] = parent

    # 各节点特有的 detail / react_round 字段
    if name == "memory_recall":
        recalled = output.get("recalled_memories", "")
        if recalled:
            meta_dict["detail"] = recalled[:600]
    elif name == "memory_extraction":
        detail = output.get("extracted_detail", "")
        if detail:
            meta_dict["detail"] = detail
    elif name in _REACT_NODES:
        rnd = _react_rounds.pop(name, 0) or output.get("react_loop_count", 0) or _cycle_counts.get(name, 0)
        meta_dict["react_round"] = rnd
        if name in ("planner", "assistant_planner"):
            thought = output.get("current_thought", "")
            if thought:
                meta_dict["detail"] = f"🧠 思考：{thought[:300]}"
        elif name in ("tool_executor", "assistant_tool_executor"):
            tcs = output.get("tool_calls", [])
            if tcs:
                args_str = json.dumps(tcs[-1].get("args", {}), ensure_ascii=False)[:200]
                meta_dict["detail"] = f"🔧 {tcs[-1].get('name','')} 入参: {args_str}"
        elif name in ("observation", "assistant_observation"):
            obs = output.get("observations", [])
            if obs:
                meta_dict["detail"] = f"📊 {obs[-1].get('result','')[:300]}"

    return meta_dict, cost


def _handle_chain_end(name: str, meta: dict, output: dict, display_name: str) -> list[AgentEvent]:
    """处理 on_chain_end：返回进度完成事件，以及复杂任务的 plan 更新事件。"""
    events = []
    meta_dict, cost = _build_chain_end_meta(name, output)

    # 助手子图内部循环节点隐藏进度事件（与 on_chain_start 一致）
    if name not in _HIDE_PROGRESS:
        events.append(AgentEvent(
            event_type="progress", name=display_name,
            status="completed", cost_ms=cost,
            metadata=meta_dict,
        ))

    # plan 事件：即使节点被隐藏也须发射（triage 生成初始计划 / planner 每轮更新状态）
    plan_steps = output.get("plan_steps")
    if plan_steps:
        from loguru import logger as _log
        _log.info(f"[plan] 节点={name} langgraph_node={meta.get('langgraph_node', '')} 步骤数={len(plan_steps)} 状态={[s.get('status') for s in plan_steps]}")
        all_done = all(s.get("status") == "done" for s in plan_steps)
        events.append(AgentEvent(
            event_type="plan", name="执行计划",
            status="completed" if all_done else "running",
            metadata={"steps": plan_steps, "parent_node": "智能助手"},
        ))

    return events


def _handle_chat_model_stream(node: str, data: dict, tags: list) -> AgentEvent | None:
    """处理 on_chat_model_stream：skip_stream 标记时忽略，否则返回 token AgentEvent。"""
    if "skip_stream" in tags:
        return None
    if node not in STREAM_NODES:
        return None
    chunk = data.get("chunk")
    if chunk and hasattr(chunk, "content") and chunk.content:
        return AgentEvent(
            event_type="token", name=node,
            status="completed", content=chunk.content,
        )
    return None


def _handle_chat_model_end(name: str, meta: dict, data: dict) -> list[AgentEvent]:
    """处理 on_chat_model_end：隐藏节点发射 plan_step_detail，其余节点发射 thought 事件。"""
    events = []
    key = f"llm:{name}"
    start = _node_starts.pop(key, _now_ts())
    cost = _now_ts() - start
    output = data.get("output", {})
    content = output.content if hasattr(output, "content") else str(output)[:500]
    raw = meta.get("langgraph_node", name)

    if raw in _HIDE_PROGRESS:
        # planner 节点不直接跳过，改为发射 plan_step_detail 供前端实时更新当前步骤 detail
        if raw == "assistant_planner":
            # 解析 LLM JSON 响应，只提取 thought 文本（去掉原始 JSON 结构）
            _thought = content
            try:
                _parsed = json.loads(content)
                if isinstance(_parsed, dict):
                    _thought = _parsed.get("thought", "") or _parsed.get("thought", content)
            except (json.JSONDecodeError, TypeError):
                pass
            events.append(AgentEvent(
                event_type="plan_step_detail", name="assistant_planner",
                status="completed", content=_thought[:2000],
            ))
        return events  # 其余隐藏节点不发射 thought 事件

    if raw in _REACT_NODES:
        thought_name = f"{NODE_LABELS[raw]} #{_cycle_counts.get(raw, 1)}"
    else:
        thought_name = NODE_LABELS.get(raw, raw)

    meta_dict: dict = {}
    parent = SUB_NODE_PARENT.get(raw, "")
    if parent:
        meta_dict["parent_node"] = parent
    if raw in _REACT_NODES:
        meta_dict["react_round"] = _react_rounds.get(raw, 1)

    events.append(AgentEvent(
        event_type="thought", name=thought_name,
        status="completed", content=content, cost_ms=cost,
        metadata=meta_dict,
    ))
    return events


def _handle_tool_start(name: str, meta: dict, data: dict) -> AgentEvent:
    """处理 on_tool_start：记录开始时间，返回工具调用开始事件。"""
    _node_starts[f"tool:{name}"] = _now_ts()
    tool_input = data.get("input", {})
    args_str = json.dumps(tool_input, ensure_ascii=False) if isinstance(tool_input, dict) else str(tool_input)
    raw_parent = meta.get("langgraph_node", "")
    # ReAct 节点需用带轮次编号的显示名，否则前端 _findStep 父节点匹配失败
    return AgentEvent(
        event_type="tool", name=name,
        status="running", content=str(tool_input)[:500],
        metadata={"tool_args": args_str, "parent_node": _get_label_parent(raw_parent)},
    )


def _handle_tool_end(name: str, meta: dict, data: dict) -> AgentEvent:
    """处理 on_tool_end：计算耗时，返回工具调用完成事件。"""
    key = f"tool:{name}"
    start = _node_starts.pop(key, _now_ts())
    cost = _now_ts() - start
    tool_output = data.get("output", "")
    output_str = str(tool_output)
    raw_parent = meta.get("langgraph_node", "")
    return AgentEvent(
        event_type="tool", name=name,
        status="completed", content=output_str[:1000], cost_ms=cost,
        metadata={
            "tool_result": output_str[:300],
            "cost_sec": round(cost / 1000, 1),
            "parent_node": _get_label_parent(raw_parent),
        },
    )


# ---- 主事件解析函数 ----

async def parse_events(stream):
    """解析 LangGraph 事件流，yield AgentEvent 实例。"""
    # 每轮对话重置计数器，确保 ReAct 节点编号从 1 开始
    _node_starts.clear()
    _cycle_counts.clear()
    _react_rounds.clear()

    async for event in stream:
        ev = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {})
        meta = event.get("metadata", {}) or {}
        if not isinstance(meta, dict):
            meta = {}
        tags = event.get("tags", [])

        # 子图节点的 name 可能带命名空间前缀（如 "assistant_agent:assistant_planner"），
        # langgraph_node 始终是原始短名，用于 NODE_LABELS 查表
        _node = name if name in NODE_LABELS else meta.get("langgraph_node", "")
        if _node and _node != name:
            from loguru import logger as _log
            _log.debug(f"[parse_events] 事件={ev} name={name} → fallback langgraph_node={_node}")

        # ReAct 循环节点：每轮独立命名（仅在 start 时递增，end 只读取）
        if _node in _REACT_NODES:
            if ev == "on_chain_start" and _node in NODE_LABELS:
                _cycle_counts[_node] = _cycle_counts.get(_node, 0) + 1
            display_name = f"{NODE_LABELS[_node]} #{_cycle_counts.get(_node, 1)}"
        elif _node:
            display_name = NODE_LABELS.get(_node, _node)
        else:
            display_name = NODE_LABELS.get(name, name)

        # --- on_chain_start: 节点开始 ---
        if ev == "on_chain_start" and _node and _node in NODE_LABELS:
            for ae in _handle_chain_start(_node, display_name, data):
                yield ae

        # --- on_chain_end: 节点完成 ---
        elif ev == "on_chain_end" and (name in NODE_LABELS or meta.get("langgraph_node", "") in NODE_LABELS):
            # 子图节点的 name 可能是 namespaced（如 "assistant_agent:assistant_planner"），
            # langgraph_node 始终是原始短名，兜底使用
            _chain_name = name if name in NODE_LABELS else meta.get("langgraph_node", name)
            if _chain_name != name:
                from loguru import logger as _log
                _log.info(f"[parse_events] on_chain_end name={name} → fallback langgraph_node={_chain_name}")
            name = _chain_name
            output = data.get("output", {}) or {}
            if not isinstance(output, dict):
                output = {}
            for ae in _handle_chain_end(name, meta, output, display_name):
                yield ae

        # --- on_chat_model_start: LLM 开始计时 ---
        elif ev == "on_chat_model_start":
            _node_starts[f"llm:{name}"] = _now_ts()

        # --- on_chat_model_stream: 流式 token ---
        elif ev == "on_chat_model_stream":
            node = meta.get("langgraph_node", "")
            ae = _handle_chat_model_stream(node, data, tags)
            if ae:
                yield ae

        # --- on_chat_model_end: LLM 完成 ---
        elif ev == "on_chat_model_end":
            for ae in _handle_chat_model_end(name, meta, data):
                yield ae

        # --- on_tool_start/end: 工具调用 ---
        elif ev == "on_tool_start":
            yield _handle_tool_start(name, meta, data)
        elif ev == "on_tool_end":
            yield _handle_tool_end(name, meta, data)

        # --- on_retriever_start/end: 检索事件 ---
        elif ev == "on_retriever_start":
            _node_starts[f"retriever:{name}"] = _now_ts()
        elif ev == "on_retriever_end":
            key = f"retriever:{name}"
            start = _node_starts.pop(key, _now_ts())
            cost = _now_ts() - start
            yield AgentEvent(
                event_type="retrieval", name=name,
                status="completed", cost_ms=cost,
            )
