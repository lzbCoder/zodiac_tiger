"""数据分析 ReAct 子图：思考 → 行动 → 观察 → 循环 → 报告生成"""

import json
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.state.report_state import ReportState
from app.tools.report_tools import REPORT_TOOLS
from app.agents.agent_utils import build_tool_desc_section
from app.prompts.loader import render

_TOOL_MAP = {t.name: t for t in REPORT_TOOLS}
_MAX_LOOPS = 5


# ---- 节点 1：任务收集 ----

async def collect_task_node(state: ReportState, config: RunnableConfig) -> dict:
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

async def planner_node(state: ReportState, config: RunnableConfig) -> dict:
    from app.factory.llm_factory import create_llm
    from app.config import settings
    from app.mcp.mcp_manager import GlobalMcpManager

    brief_obs = [
        {"tool": o.get("tool", "unknown"), "summary": str(o.get("result", ""))[:300]}
        for o in state.get("observations", [])
    ]
    obs_text = json.dumps(brief_obs, ensure_ascii=False)
    loop = state.get("react_loop_count", 0)
    enable_search = config["configurable"].get("enable_search", False)

    # 静态工具（按 enable_search 过滤 tavily）+ MCP 动态工具
    static_tools = {
        t.name: t for t in REPORT_TOOLS
        if t.name != "tavily_search_report" or enable_search
    }
    try:
        mcp_tools = {t.name: t for t in await GlobalMcpManager.build_tools_for_agent("report_agent")}
    except Exception:
        mcp_tools = {}
    all_tools = {**static_tools, **mcp_tools}
    tool_section = build_tool_desc_section(all_tools)

    # 技能系统提示词注入
    from app.skills.manager import GlobalSkillManager
    try:
        skill_list = await GlobalSkillManager.get_skills_for_agent("report_agent")
    except Exception:
        skill_list = []
    skill_context = ""
    if skill_list:
        parts = [f"【{s['display_name']}】\n{s['system_prompt']}"
                 for s in skill_list if s.get("system_prompt")]
        if parts:
            skill_context = "\n\n已加载本地技能（请参考其指令执行）：\n" + "\n\n".join(parts)

    prompt = render(
        "report_planner",
        task=state.get("task", ""),
        skill_context=skill_context,
        obs_text=obs_text if obs_text != "[]" else "无",
        loop=loop,
        max_loops=_MAX_LOOPS,
        tool_section=tool_section,
    )

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    resp = await llm.ainvoke(prompt)
    try:
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        plan = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Planner JSON 解析失败: {resp.content[:200]}")
        plan = {"thought": "无法解析思考结果，直接生成报告", "action": None, "action_input": {}, "finish": True}

    return {
        "current_thought": plan.get("thought", ""),
        "current_action": plan.get("action"),                     # 保存工具名
        "current_action_input": plan.get("action_input", {}),     # 保存工具参数
        "is_finish": plan.get("finish", False) or loop + 1 >= _MAX_LOOPS,
        "react_loop_count": loop + 1,
    }


# ---- 节点 3：工具执行 ----

async def tool_executor_node(state: ReportState, config: RunnableConfig) -> dict:
    from app.mcp.mcp_manager import GlobalMcpManager

    # 直接读取 planner 保存的工具调用信息，无需再次调用 LLM 提取
    tool_name = state.get("current_action", "") or ""
    tool_args = state.get("current_action_input", {}) or {}

    # 静态工具优先，然后回退到 MCP 动态工具
    tool = _TOOL_MAP.get(tool_name)
    if not tool:
        try:
            mcp_tools = {t.name: t for t in await GlobalMcpManager.build_tools_for_agent("report_agent")}
            tool = mcp_tools.get(tool_name)
        except Exception:
            tool = None

    if not tool:
        logger.warning(f"[tool_executor] 无效工具: {tool_name}")
        return {
            "observation_result": f"未知工具: {tool_name}",
            "tool_calls": [],
            "react_loop_count": state.get("react_loop_count", 0)
        }

    try:
        if hasattr(tool, "ainvoke"):
            result = str(await tool.ainvoke(tool_args))
        else:
            result = str(tool.invoke(tool_args))
    except Exception as e:
        result = f"工具执行失败: {e}"

    return {
        "observation_result": result,
        "tool_calls": [{"name": tool_name, "args": tool_args}],
        "react_loop_count": state.get("react_loop_count", 0),
    }


# ---- 节点 4：观察记录 ----

async def observation_node(state: ReportState, config: RunnableConfig) -> dict:
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


# ---- 节点 5：报告生成 ----

async def report_generator_node(state: ReportState, config: RunnableConfig) -> dict:
    from app.factory.llm_factory import create_llm
    from app.config import settings

    raw_obs = state.get("observations", [])
    obs_sections = []
    for o in raw_obs:
        obs_sections.append(f"【{o.get('tool', 'unknown')}】\n{str(o.get('result', ''))}")
    obs_text = "\n\n".join(obs_sections) if obs_sections else "无"
    prompt = render("report_generator", task=state.get("task", ""), obs_text=obs_text)

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    resp_content = ""
    async for chunk in llm.astream(prompt):
        if chunk.content:
            resp_content += chunk.content
    return {
        "final_report": resp_content,
        "generate_content": resp_content,
        "generate_format": state.get("generate_format", ""),
        "messages": [{"role": "ai", "content": resp_content}],
    }


# ---- 路由 ----

def route_after_planner(state: ReportState) -> str:
    if state.get("is_finish", False):
        return "report_generator"
    if state.get("react_loop_count", 0) >= _MAX_LOOPS:
        return "report_generator"
    return "tool_executor"


# ---- 构建子图 ----

def build_report_subgraph() -> StateGraph:
    sub = StateGraph(ReportState)

    sub.add_node("collect_task", collect_task_node)
    sub.add_node("planner", planner_node)
    sub.add_node("tool_executor", tool_executor_node)
    sub.add_node("observation", observation_node)
    sub.add_node("report_generator", report_generator_node)

    sub.set_entry_point("collect_task")
    sub.add_edge("collect_task", "planner")

    sub.add_conditional_edges("planner", route_after_planner, {
        "tool_executor": "tool_executor",
        "report_generator": "report_generator",
    })

    sub.add_edge("tool_executor", "observation")
    sub.add_edge("observation", "planner")
    sub.add_edge("report_generator", END)

    return sub.compile()
