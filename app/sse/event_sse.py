"""LangGraph 事件流 → AgentEvent → SSE 消息转换"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from loguru import logger as _log


_CST = timezone(timedelta(hours=8))

INTENT_LABELS: dict[str, str] = {
    "chat": "聊天", "travel": "旅游", "assistant": "助手",
}

NODE_LABELS: dict[str, str] = {
    # 主流程节点
    "memory_recall":              "记忆召回",
    "task_manager":               "任务管理",
    "dispatcher":                 "意图识别",
    "travel_agent":               "旅游规划",
    "assistant_agent":            "智能助手",
    "chat_agent":                 "对话聊天",
    "document_agent":             "文档生成",
    "artifact_store":             "产物存储",
    "memory_extraction":          "记忆提取",
    # 旅游规划子图-内部节点
    "collect_params":             "参数提取",
    "validate_params":            "参数校验",
    "query_geo":                  "地理编码",
    "query_weather":              "天气查询",
    "query_route":                "路线查询",
    "generate_plan":              "行程生成",
    # 综合助手子图-内部节点
    "assistant_collect_task":     "任务收集",
    "assistant_artifact_export":  "产物导出",
    "assistant_triage":           "任务分析",
    "assistant_activate_skill":   "技能激活",
    "assistant_tool_router":      "工具路由",
    "assistant_planner":          "分析思考",
    "assistant_tool_executor":    "工具执行",
    "assistant_tool_manager":     "工具管理",
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
    # 综合助手子图：子级 --> 父级
    "assistant_collect_task":     "智能助手",
    "assistant_artifact_export":  "智能助手",
    "assistant_triage":           "智能助手",
    "assistant_activate_skill":   "智能助手",
    "assistant_tool_router":      "智能助手",
    "assistant_planner":          "智能助手",
    "assistant_tool_executor":    "智能助手",
    "assistant_tool_manager":     "智能助手",
    "assistant_answer_generator": "智能助手",
}

# 流式输出结果到主回复的节点（token → 正文）
STREAM_NODES = {"chat_agent", "document_agent",
                "generate_plan", "assistant_answer_generator"}

# 思考型节点：LLM 流式输出作为「思考」附属条目展示（thinking / thinking_token）
THINKING_NODES = {
    "collect_params",
    "assistant_triage", "assistant_activate_skill", "assistant_planner",
}


def _now_ts() -> int:
    return int(datetime.now(_CST).timestamp() * 1000)


# ---- 统一事件模型 ----

@dataclass
class AgentEvent:
    event_type: str       # progress | tool | thinking | thinking_token | token | retrieval | ...
    name: str             # 步骤/工具/模型名
    status: str           # running | completed | error
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


# ---- 单次调用状态（取代模块级全局字典，确保并发安全）----

@dataclass
class _ParseState:
    node_starts:  dict[str, int] = field(default_factory=dict)  # node_name → start_timestamp
    cycle_counts: dict[str, int] = field(default_factory=dict)  # ReAct 节点出现次数
    react_rounds: dict[str, int] = field(default_factory=dict)  # ReAct 节点当前轮次（start 时写入，end 时读取）
    attempts:     dict[str, int] = field(default_factory=dict)  # 节点重试计数（连续 start 无 end = 重试递增，end 复位）


# ReAct 循环节点（每轮重复出现，靠 react_round 字段区分，不在名称上加 #n）
_REACT_NODES = {
    "assistant_planner", "assistant_tool_executor",
}


# ---- parse_events 私有辅助函数 ----

def _node_kind(node: str) -> str:
    """主阶段(stage) vs 子步骤(substep)：子图内部节点为 substep，其余为 stage。"""
    return "substep" if node in SUB_NODE_PARENT else "stage"


def _get_label_parent(raw_parent: str) -> str:
    """将节点原始名称转换为纯显示名（ReAct 节点不再附加 #n，靠 react_round 区分轮次）。"""
    return NODE_LABELS.get(raw_parent, raw_parent)


def _handle_chain_start(_node: str, display_name: str, data: dict, state: _ParseState) -> list[AgentEvent]:
    """处理 on_chain_start：记录开始时间，返回进度事件。"""
    state.node_starts[_node] = _now_ts()
    # 重试计数：连续 start 无 end 视为重试（end 时复位）
    state.attempts[_node] = state.attempts.get(_node, 0) + 1
    parent = SUB_NODE_PARENT.get(_node, "")
    meta_dict: dict = {"node_kind": _node_kind(_node), "attempt": state.attempts[_node]}
    if parent:
        meta_dict["parent_node"] = parent

    # ReAct 节点：从 input 读取 react_loop_count，确保与 end 时一致
    if _node in _REACT_NODES:
        input_round = data.get("input", {}).get("react_loop_count", 0)
        react_round = input_round + 1 if _node == "planner" else input_round  # planner 自增 1，其余透传
        state.react_rounds[_node] = react_round
        meta_dict["react_round"] = react_round

    return [AgentEvent(
        event_type="progress", name=display_name,
        status="running", content=_node,
        metadata=meta_dict,
    )]


def _build_chain_end_meta(name: str, output: dict, state: _ParseState) -> tuple[dict, int]:
    """计算节点耗时，构建 on_chain_end 的 meta_dict，含 intent/detail/react_round 等字段。"""
    start = state.node_starts.pop(name, _now_ts())
    cost = _now_ts() - start

    raw_intent = output.get("intent", "")
    label_intent = INTENT_LABELS.get(raw_intent, raw_intent)
    meta_dict: dict = {"node_kind": _node_kind(name), "attempt": state.attempts.pop(name, 1)}
    if raw_intent and name == "dispatcher":
        meta_dict["intent"] = label_intent

    parent = SUB_NODE_PARENT.get(name, "")
    if parent:
        meta_dict["parent_node"] = parent

    # 各节点特有的 detail / react_round 字段
    if name in ("collect_task", "assistant_collect_task"):
        task = output.get("task", "")
        if task:
            meta_dict["detail"] = f"收集到的任务：\n{task[:600]}"
    elif name in ("activate_skill", "assistant_activate_skill"):
        skill_infos = output.get("_activated_skill_infos", [])
        if skill_infos:
            lines = "\n".join(f"- {s.get('display_name', '')}：{s.get('skill_desc', '')}" for s in skill_infos)
            meta_dict["detail"] = f"已激活 {len(skill_infos)} 条技能：\n{lines}"
        else:
            meta_dict["detail"] = "未匹配到适合当前任务的技能"
    elif name == "memory_recall":
        recalled = output.get("recalled_memories", "")
        if recalled:
            meta_dict["detail"] = recalled[:600]
    elif name == "memory_extraction":
        detail = output.get("extracted_detail", "")
        if detail:
            meta_dict["detail"] = detail
    elif name in ("tool_router", "assistant_tool_router"):
        names = output.get("activated_tool_names", [])
        meta_dict["detail"] = f"已选择工具：{', '.join(names) if names else '无'}"
    elif name in ("tool_manager", "assistant_tool_manager"):
        if output.get("tool_search_exhausted"):
            meta_dict["detail"] = "未找到相关工具，转为基于现有能力作答"
        else:
            names = output.get("activated_tool_names", [])
            meta_dict["detail"] = f"已补充工具，当前可用：{', '.join(names) if names else '无'}"
    elif name in _REACT_NODES:
        # 用 None 哨兵区分"未记录"与合法的 0 值，避免 or 链将 0 误判为无效
        rnd = state.react_rounds.pop(name, None)
        if rnd is None:
            rnd = output.get("react_loop_count") or state.cycle_counts.get(name, 1)
        meta_dict["react_round"] = rnd
        # 分析思考(planner) → 思考条目；工具执行(tool_executor) → 工具调用条目（均由专门事件承载）

    return meta_dict, cost


def _handle_chain_end(name: str, meta: dict, output: dict, display_name: str, state: _ParseState) -> list[AgentEvent]:
    """处理 on_chain_end：返回进度完成事件。"""
    meta_dict, cost = _build_chain_end_meta(name, output, state)
    return [AgentEvent(
        event_type="progress", name=display_name,
        status="completed", cost_ms=cost,
        metadata=meta_dict,
    )]


def _thinking_meta(node: str, state: _ParseState) -> dict:
    """构建思考条目的 metadata：挂载到的子步骤显示名 + ReAct 轮次。"""
    meta_dict = {"attach_to": NODE_LABELS.get(node, node)}
    if node in _REACT_NODES:
        meta_dict["react_round"] = state.react_rounds.get(node, 1)
    return meta_dict


def _handle_chat_model_start(node: str, state: _ParseState) -> AgentEvent | None:
    """处理 on_chat_model_start：思考节点记录计时并发射 thinking(running)。"""
    if node not in THINKING_NODES:
        return None
    state.node_starts[f"think:{node}"] = _now_ts()
    return AgentEvent(
        event_type="thinking", name=NODE_LABELS.get(node, node),
        status="running", metadata=_thinking_meta(node, state),
    )


def _chunk_reasoning(obj) -> str:
    """从 chunk/输出对象提取 reasoning_content（qwen3 思考模式独立通道）。"""
    ak = getattr(obj, "additional_kwargs", None) or {}
    return ak.get("reasoning_content") or ""


def _handle_chat_model_stream(node: str, data: dict, tags: list, state: _ParseState) -> AgentEvent | None:
    """处理 on_chat_model_stream：STREAM_NODES→正文 token；THINKING_NODES→thinking_token。

    思考节点双通道：content（正常文本）或 reasoning_content（推理通道）任一有值即作思考流。
    """
    if "skip_stream" in tags:
        return None
    chunk = data.get("chunk")
    if not chunk:
        return None
    content = getattr(chunk, "content", "") or ""

    if node in STREAM_NODES:
        if not content:
            return None
        return AgentEvent(
            event_type="token", name=node,
            status="running", content=content,
        )
    if node in THINKING_NODES:
        text = content or _chunk_reasoning(chunk)   # 双通道
        if not text:
            return None
        return AgentEvent(
            event_type="thinking_token", name=NODE_LABELS.get(node, node),
            status="running", content=text,
            metadata=_thinking_meta(node, state),
        )
    return None


def _handle_chat_model_end(name: str, meta: dict, data: dict, state: _ParseState) -> list[AgentEvent]:
    """处理 on_chat_model_end：思考节点发射 thinking(completed) 含完整内容与耗时。"""
    node = meta.get("langgraph_node", name)
    if node not in THINKING_NODES:
        return []
    start = state.node_starts.pop(f"think:{node}", _now_ts())
    cost = _now_ts() - start
    output = data.get("output", {})
    if hasattr(output, "content"):
        content = output.content or _chunk_reasoning(output)   # 双通道：content 为空回退 reasoning
    else:
        content = str(output)[:2000]
    return [AgentEvent(
        event_type="thinking", name=NODE_LABELS.get(node, node),
        status="completed", content=content, cost_ms=cost,
        metadata=_thinking_meta(node, state),
    )]


def _tool_attach_meta(meta: dict, state: _ParseState) -> dict:
    """工具条目的挂载信息：attach_to=纯节点显示名（如"工具执行"）+ ReAct 轮次。"""
    raw_parent = meta.get("langgraph_node", "")
    attach: dict = {"attach_to": _get_label_parent(raw_parent)}
    if raw_parent in _REACT_NODES:
        attach["react_round"] = state.react_rounds.get(raw_parent, 1)
    return attach


def _handle_tool_start(name: str, meta: dict, data: dict, state: _ParseState, run_id: str = "") -> AgentEvent:
    """处理 on_tool_start：记录开始时间，返回工具调用开始事件。

    用 run_id 作计时键与前端去重键（tool_run_id），支持同名工具并行调用各占一条。
    """
    state.node_starts[f"tool:{run_id or name}"] = _now_ts()
    tool_input = data.get("input", {})
    args_str = json.dumps(tool_input, ensure_ascii=False) if isinstance(tool_input, dict) else str(tool_input)
    return AgentEvent(
        event_type="tool", name=name,
        status="running", content=str(tool_input)[:2000],
        metadata={"tool_name": name, "tool_run_id": run_id, "tool_args": args_str[:2000],
                  **_tool_attach_meta(meta, state)},
    )


def _handle_tool_end(name: str, meta: dict, data: dict, state: _ParseState, run_id: str = "") -> AgentEvent:
    """处理 on_tool_end：计算耗时，返回工具调用完成事件。"""
    start = state.node_starts.pop(f"tool:{run_id or name}", _now_ts())
    cost = _now_ts() - start
    tool_output = data.get("output", "")
    output_str = str(tool_output)
    status = "error" if output_str.startswith("工具执行失败") or output_str.startswith("未知工具") else "completed"
    return AgentEvent(
        event_type="tool", name=name,
        status=status, content=output_str[:2000], cost_ms=cost,
        metadata={
            "tool_name": name,
            "tool_run_id": run_id,
            "tool_result": output_str[:2000],
            "cost_sec": round(cost / 1000, 1),
            **_tool_attach_meta(meta, state),
        },
    )


# ---- 主事件解析函数 ----

async def parse_events(stream):
    """解析 LangGraph 事件流，yield AgentEvent 实例。"""
    state = _ParseState()  # 每次调用独立实例，并发安全

    async for event in stream:
        ev = event.get("event", "")
        name = event.get("name", "")
        data = event.get("data", {})
        meta = event.get("metadata", {}) or {}
        if not isinstance(meta, dict):
            meta = {}
        tags = event.get("tags", [])

        # _node 只在 name 就是节点本身时才赋值：
        #   1) name 直接在 NODE_LABELS（如 "query_geo"）
        #   2) name 是带命名空间前缀的子图节点（如 "assistant_agent:assistant_planner"），
        #      此时 meta.langgraph_node = "assistant_planner"，且 name.endswith(":assistant_planner")
        # 以上两种情况以外（如工具 on_chain_start/end：name="amap_geocode", langgraph_node="query_geo"），
        # _node 留空，避免把工具的链式事件误判为节点事件。
        _ln = meta.get("langgraph_node", "")
        if name in NODE_LABELS:
            _node = name
        elif _ln in NODE_LABELS and (name == _ln or name.endswith(f":{_ln}")):
            _node = _ln
            _log.debug(f"[parse_events] 事件={ev} name={name} → namespaced fallback _node={_node}")
        else:
            _node = ""

        # ReAct 循环节点：名称保持纯文本（不加 #n），仅在 start 时递增 cycle_counts 以写 react_round
        if _node in _REACT_NODES and ev == "on_chain_start" and _node in NODE_LABELS:
            state.cycle_counts[_node] = state.cycle_counts.get(_node, 0) + 1
        if _node:
            display_name = NODE_LABELS.get(_node, _node)
        else:
            display_name = NODE_LABELS.get(name, name)

        # --- on_chain_start: 节点开始 ---
        if ev == "on_chain_start" and _node and _node in NODE_LABELS:
            for ae in _handle_chain_start(_node, display_name, data, state):
                yield ae

        # --- on_chain_end: 节点完成 ---
        elif ev == "on_chain_end" and _node:
            output = data.get("output", {}) or {}
            if not isinstance(output, dict):
                output = {}
            for ae in _handle_chain_end(_node, meta, output, display_name, state):
                yield ae

        # --- on_chat_model_start: 思考节点发射 thinking(running) ---
        elif ev == "on_chat_model_start":
            ae = _handle_chat_model_start(meta.get("langgraph_node", ""), state)
            if ae:
                yield ae

        # --- on_chat_model_stream: 正文 token / 思考增量 ---
        elif ev == "on_chat_model_stream":
            node = meta.get("langgraph_node", "")
            ae = _handle_chat_model_stream(node, data, tags, state)
            if ae:
                yield ae

        # --- on_chat_model_end: LLM 完成 ---
        elif ev == "on_chat_model_end":
            for ae in _handle_chat_model_end(name, meta, data, state):
                yield ae

        # --- on_custom_event: document_agent 确定性文件信息流式上屏（不经 LLM） ---
        elif ev == "on_custom_event" and name == "doc_token":
            yield AgentEvent(
                event_type="token", name="document_agent",
                status="running", content=data.get("content", "") if isinstance(data, dict) else "",
            )

        # --- on_tool_start/end: 工具调用 ---
        elif ev == "on_tool_start":
            yield _handle_tool_start(name, meta, data, state, event.get("run_id", ""))
        elif ev == "on_tool_end":
            yield _handle_tool_end(name, meta, data, state, event.get("run_id", ""))

        # --- on_retriever_start/end: 检索事件 ---
        elif ev == "on_retriever_start":
            state.node_starts[f"retriever:{name}"] = _now_ts()
        elif ev == "on_retriever_end":
            key = f"retriever:{name}"
            start = state.node_starts.pop(key, _now_ts())
            cost = _now_ts() - start
            yield AgentEvent(
                event_type="retrieval", name=name,
                status="completed", cost_ms=cost,
            )
