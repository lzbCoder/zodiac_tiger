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
    "memory_recall":      "记忆召回",
    "dispatcher":         "意图识别",
    "report_agent":       "报表生成",
    "travel_agent":       "旅游规划",
    "memory_extraction":  "记忆提取",
    "document_agent":     "文档生成",
    "chat_agent":         "对话聊天",
    # 旅游子图内部节点
    "collect_params":     "参数提取",
    "validate_params":    "参数校验",
    "query_geo":          "地理编码",
    "query_weather":      "天气查询",
    "query_route":        "路线查询",
    "generate_plan":      "行程生成",
    # 数据分析子图
    "collect_task":       "任务收集",
    "planner":            "分析思考",
    "tool_executor":      "工具执行",
    "observation":        "数据观察",
    "report_generator":   "报告生成",
    # 综合助手子图
    "assistant_agent":            "智能助手",
    "assistant_collect_task":     "任务收集",
    "assistant_planner":          "分析思考",
    "assistant_tool_executor":    "工具执行",
    "assistant_observation":      "数据观察",
    "assistant_answer_generator": "回答生成",
}

# 子图节点 → 父节点映射（用于 tool/children 归属）
SUB_NODE_PARENT: dict[str, str] = {
    "collect_params": "旅游规划",
    "validate_params": "旅游规划",
    "query_geo": "旅游规划",
    "query_weather": "旅游规划",
    "query_route": "旅游规划",
    "generate_plan": "旅游规划",
    "collect_task": "报表生成",
    "planner": "报表生成",
    "tool_executor": "报表生成",
    "observation": "报表生成",
    "report_generator": "报表生成",
    # 综合助手子图
    "assistant_collect_task":     "智能助手",
    "assistant_planner":          "智能助手",
    "assistant_tool_executor":    "智能助手",
    "assistant_observation":      "智能助手",
    "assistant_answer_generator": "智能助手",
}

STREAM_NODES = {"chat_agent", "document_agent", "generate_plan", "report_generator", "assistant_answer_generator"}


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

_node_starts: dict[str, int] = {}  # node_name → start_timestamp
_cycle_counts: dict[str, int] = {}  # ReAct 节点出现次数
_react_rounds: dict[str, int] = {}  # ReAct 节点当前轮次（start 时写入，end 时读取）

# ReAct 循环节点（每轮独立命名，不合并）
_REACT_NODES = {
    "planner", "tool_executor", "observation",
    "assistant_planner", "assistant_tool_executor", "assistant_observation",
}


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
        meta = event.get("metadata", {})
        tags = event.get("tags", [])

        # ReAct 循环节点：每轮独立命名（仅在 start 时递增，end 只读取）
        if name in _REACT_NODES:
            if ev == "on_chain_start" and name in NODE_LABELS:
                _cycle_counts[name] = _cycle_counts.get(name, 0) + 1
            display_name = f"{NODE_LABELS[name]} #{_cycle_counts.get(name, 1)}"
        else:
            display_name = NODE_LABELS.get(name, name)

        # --- progress: 节点开始 ---
        if ev == "on_chain_start" and name in NODE_LABELS:
            _node_starts[name] = _now_ts()
            parent = SUB_NODE_PARENT.get(name, "")
            meta_dict = {"parent_node": parent} if parent else {}
            # ReAct 节点：从 input 读取 react_loop_count，确保与 end 一致
            if name in _REACT_NODES:
                input_round = data.get("input", {}).get("react_loop_count", 0)
                if name == "planner":
                    react_round = input_round + 1  # planner 自增 1
                else:
                    react_round = input_round      # tool_executor/observation 透传
                _react_rounds[name] = react_round
                meta_dict["react_round"] = react_round
            yield AgentEvent(
                event_type="progress", name=display_name,
                status="running", content=name,
                metadata=meta_dict,
            )

        # --- progress: 节点完成 ---
        elif ev == "on_chain_end" and name in NODE_LABELS:
            start = _node_starts.pop(name, _now_ts())
            cost = _now_ts() - start
            output = data.get("output", {})
            raw_intent = output.get("intent", "")
            label_intent = INTENT_LABELS.get(raw_intent, raw_intent)
            meta_dict = {"intent": label_intent} if raw_intent and name == "dispatcher" else {}
            parent = SUB_NODE_PARENT.get(name, "")
            if parent:
                meta_dict["parent_node"] = parent

            # 详情 + react_round
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
            elif name == "assistant_answer_generator":
                answer = output.get("final_answer", "")
                if answer:
                    meta_dict["detail"] = answer[:300]

            yield AgentEvent(
                event_type="progress", name=display_name,
                status="completed", cost_ms=cost,
                metadata=meta_dict,
            )

        # --- thought: 聊天模型 开始 ---
        elif ev == "on_chat_model_start":
            _node_starts[f"llm:{name}"] = _now_ts()

        # --- token: 流式正文 ---
        elif ev == "on_chat_model_stream":
            if "skip_stream" in tags:
                continue
            node = meta.get("langgraph_node", "")
            if node in STREAM_NODES:
                chunk = data.get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    yield AgentEvent(
                        event_type="token", name=node,
                        status="completed", content=chunk.content,
                    )

        # --- thought: 聊天模型 完成 ---
        elif ev == "on_chat_model_end":
            key = f"llm:{name}"
            start = _node_starts.pop(key, _now_ts())
            cost = _now_ts() - start
            output = data.get("output", {})
            content = output.content if hasattr(output, "content") else str(output)[:500]
            raw = meta.get("langgraph_node", name)
            if raw in _REACT_NODES:
                thought_name = f"{NODE_LABELS[raw]} #{_cycle_counts.get(raw, 1)}"
            else:
                thought_name = NODE_LABELS.get(raw, raw)
            meta_dict = {}
            parent = SUB_NODE_PARENT.get(raw, "")
            if parent:
                meta_dict["parent_node"] = parent
            if raw in _REACT_NODES:
                meta_dict["react_round"] = _react_rounds.get(raw, 1)
            yield AgentEvent(
                event_type="thought", name=thought_name,
                status="completed", content=content, cost_ms=cost,
                metadata=meta_dict,
            )

        # --- tool: 工具调用 ---
        elif ev == "on_tool_start":
            _node_starts[f"tool:{name}"] = _now_ts()
            tool_input = data.get("input", {})
            args_str = json.dumps(tool_input, ensure_ascii=False) if isinstance(tool_input, dict) else str(tool_input)
            raw_parent = meta.get("langgraph_node", "")
            # ReAct 节点需用带轮次编号的显示名，否则 _findStep 父节点匹配失败
            if raw_parent in _REACT_NODES:
                label_parent = f"{NODE_LABELS[raw_parent]} #{_cycle_counts.get(raw_parent, 1)}"
            else:
                label_parent = NODE_LABELS.get(raw_parent, raw_parent)
            yield AgentEvent(
                event_type="tool", name=name,
                status="running", content=str(tool_input)[:500],
                metadata={"tool_args": args_str, "parent_node": label_parent},
            )
        elif ev == "on_tool_end":
            key = f"tool:{name}"
            start = _node_starts.pop(key, _now_ts())
            cost = _now_ts() - start
            tool_output = data.get("output", "")
            output_str = str(tool_output)
            raw_parent = meta.get("langgraph_node", "")
            if raw_parent in _REACT_NODES:
                label_parent = f"{NODE_LABELS[raw_parent]} #{_cycle_counts.get(raw_parent, 1)}"
            else:
                label_parent = NODE_LABELS.get(raw_parent, raw_parent)
            yield AgentEvent(
                event_type="tool", name=name,
                status="completed", content=output_str[:1000], cost_ms=cost,
                metadata={
                    "tool_result": output_str[:300],
                    "cost_sec": round(cost / 1000, 1),
                    "parent_node": label_parent,
                },
            )

        # --- retrieval: 检索事件 ---
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
