"""综合助手 ReAct 子图：思考 → 行动 → 观察 → 循环 → 回答生成"""

import json
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.state.assistant_state import AssistantState
from app.tools.web_search import web_search as web_search_tool

_MAX_LOOPS = 10


def _build_tool_map(enable_search: bool) -> dict:
    if enable_search:
        return {web_search_tool.name: web_search_tool}
    return {}


# ---- 节点 1：任务收集 ----

async def collect_task_node(state: AssistantState, config: RunnableConfig) -> dict:
    user_msg = ""
    for m in reversed(state.get("messages", [])):
        if hasattr(m, "type") and m.type == "human":
            user_msg = m.content
            break
    return {
        "task": user_msg,
        "observations": [],
        "tool_calls": [],
        "react_loop_count": 0,
        "is_finish": False,
    }


# ---- 节点 2：Planner（思考决策） ----

async def planner_node(state: AssistantState, config: RunnableConfig) -> dict:
    from app.factory.llm_factory import create_llm
    from app.config import settings

    enable_search = config["configurable"].get("enable_search", False)
    loop = state.get("react_loop_count", 0)

    brief_obs = [
        {"tool": o.get("tool", "unknown"), "summary": str(o.get("result", ""))[:300]}
        for o in state.get("observations", [])
    ]
    obs_text = json.dumps(brief_obs, ensure_ascii=False)

    search_tool_desc = (
        "\n- web_search：联网搜索最新信息，参数 {\"query\": \"搜索词\"}"
        if enable_search else ""
    )
    no_tool_note = "" if enable_search else "\n注意：当前无可用工具，请直接基于知识回答，将 finish 设为 true。"

    prompt = f"""你是越群山综合智能助手，负责处理报表、旅游之外的各类任务。当前任务：{state.get('task', '')}

历史观察结果：{obs_text if obs_text != '[]' else '无'}
已执行循环次数：{loop} / {_MAX_LOOPS}

请分析并决定下一步行动。返回纯 JSON：

{{"thought": "你的分析思考", "action": "工具名或null", "action_input": {{}}, "finish": true/false}}

可用工具：{search_tool_desc if enable_search else '（无）'}{search_tool_desc}{no_tool_note}

规则：
1. 需要实时信息、最新数据 → 调用 web_search
2. 信息足够或无工具可用 → finish=true, action=null
3. 超过 {_MAX_LOOPS} 轮强制结束
4. 只返回 JSON，不要其他内容"""

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    resp = await llm.ainvoke(prompt)
    try:
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        plan = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"[assistant] Planner JSON 解析失败: {resp.content[:200]}")
        plan = {"thought": "无法解析思考结果，直接生成回答", "action": None, "action_input": {}, "finish": True}

    return {
        "current_thought": plan.get("thought", ""),
        "current_action": plan.get("action"),
        "current_action_input": plan.get("action_input", {}),
        "is_finish": plan.get("finish", False) or loop + 1 >= _MAX_LOOPS,
        "react_loop_count": loop + 1,
    }


# ---- 节点 3：工具执行 ----

async def tool_executor_node(state: AssistantState, config: RunnableConfig) -> dict:
    enable_search = config["configurable"].get("enable_search", False)
    tool_map = _build_tool_map(enable_search)

    tool_name = state.get("current_action", "") or ""
    tool_args = state.get("current_action_input", {}) or {}
    tool = tool_map.get(tool_name)

    if not tool:
        logger.warning(f"[assistant] 无效工具: {tool_name}")
        return {
            "observation_result": f"未知工具: {tool_name}",
            "tool_calls": [],
            "react_loop_count": state.get("react_loop_count", 0),
        }

    try:
        result = str(tool.invoke(tool_args))
    except Exception as e:
        result = f"工具执行失败: {e}"

    return {
        "observation_result": result,
        "tool_calls": [{"name": tool_name, "args": tool_args}],
        "react_loop_count": state.get("react_loop_count", 0),
    }


# ---- 节点 4：观察记录 ----

async def observation_node(state: AssistantState, config: RunnableConfig) -> dict:
    tcs = state.get("tool_calls", [])
    result = state.get("observation_result", "")
    if not tcs or not result:
        return {"react_loop_count": state.get("react_loop_count", 0)}
    tc = tcs[-1]
    obs = {
        "tool": tc.get("name", "unknown"),
        "args": tc.get("args", {}),
        "result": result[:3000],
    }
    observations = list(state.get("observations", [])) + [obs]
    return {
        "observations": observations,
        "react_loop_count": state.get("react_loop_count", 0),
    }


# ---- 节点 5：回答生成 ----

async def answer_generator_node(state: AssistantState, config: RunnableConfig) -> dict:
    from app.factory.llm_factory import create_llm
    from app.config import settings

    raw_obs = state.get("observations", [])
    obs_sections = []
    for o in raw_obs:
        obs_sections.append(f"【{o.get('tool', 'unknown')}】\n{str(o.get('result', ''))}")
    obs_text = "\n\n".join(obs_sections) if obs_sections else "无"

    prompt = f"""你是越群山综合智能助手，请根据以下信息回答用户问题。

用户任务：{state.get('task', '')}

收集到的信息：{obs_text}

请用中文回答，专业、友好、简洁。如有搜索结果，请优先基于搜索结果作答。"""

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    resp = await llm.ainvoke(prompt)
    answer = resp.content

    return {
        "final_answer": answer,
        "generate_content": answer,
        "generate_format": state.get("generate_format", ""),
        "messages": [{"role": "ai", "content": answer}],
    }


# ---- 路由 ----

def route_after_planner(state: AssistantState) -> str:
    if state.get("is_finish", False):
        return "assistant_answer_generator"
    if state.get("react_loop_count", 0) >= _MAX_LOOPS:
        return "assistant_answer_generator"
    return "assistant_tool_executor"


# ---- 构建子图 ----

def add_assistant_agent() -> StateGraph:
    sub = StateGraph(AssistantState)

    sub.add_node("assistant_collect_task",     collect_task_node)
    sub.add_node("assistant_planner",          planner_node)
    sub.add_node("assistant_tool_executor",    tool_executor_node)
    sub.add_node("assistant_observation",      observation_node)
    sub.add_node("assistant_answer_generator", answer_generator_node)

    sub.set_entry_point("assistant_collect_task")
    sub.add_edge("assistant_collect_task", "assistant_planner")

    sub.add_conditional_edges("assistant_planner", route_after_planner, {
        "assistant_tool_executor":    "assistant_tool_executor",
        "assistant_answer_generator": "assistant_answer_generator",
    })

    sub.add_edge("assistant_tool_executor", "assistant_observation")
    sub.add_edge("assistant_observation",   "assistant_planner")
    sub.add_edge("assistant_answer_generator", END)

    return sub.compile()
