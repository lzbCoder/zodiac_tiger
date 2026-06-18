"""数据分析 ReAct 子图：思考 → 行动 → 观察 → 循环 → 报告生成"""

import json
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.config import settings
from app.db.session import get_db_session
from app.factory.llm_factory import create_llm
from app.mcp.mcp_manager import GlobalMcpManager
from app.models.agent_skill_rel import AgentSkillRel
from app.models.skill_info import SkillInfo
from app.prompts.loader import render
from app.skills.registry import SkillRegistry
from app.state.report_state import ReportState
from app.tools.report_tools import REPORT_TOOLS
from app.agents.agent_utils import build_tool_desc_section

from sqlalchemy import select

_TOOL_MAP = {t.name: t for t in REPORT_TOOLS}
_MAX_LOOPS = 5


def _compile_skills(xml_bodies: list[str]) -> str:
    """将多个技能 XML 合并为统一内容（代码合并，不调用 LLM）。"""
    return "\n\n".join(xml_bodies)


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
        "skill_context": "",
        "activated_skill_keys": [],
    }


# ---- 节点 2：技能激活 ----

async def activate_skill_node(state: ReportState, config: RunnableConfig) -> dict:
    """查询报表 Agent 绑定的技能目录，LLM 按任务筛选，从 Redis 获取激活内容。"""
    async with get_db_session() as session:
        rows = (await session.execute(
            select(AgentSkillRel.skill_key, AgentSkillRel.skill_desc)
            .where(AgentSkillRel.agent_code == "report_agent")
        )).all()

    if not rows:
        return {"skill_context": "", "activated_skill_keys": [], "_activated_skill_infos": []}

    catalog = [{"skill_key": r.skill_key, "skill_desc": r.skill_desc or ""} for r in rows]
    catalog_text = "\n".join(f"- {c['skill_key']}：{c['skill_desc']}" for c in catalog)

    prompt = render("skill_activate", task=state.get("task", ""), catalog_text=catalog_text)
    llm = create_llm(settings.INTENT_MODEL, streaming=False, tags=["skip_stream"])
    resp = await llm.ainvoke(prompt)
    try:
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        selected_keys = json.loads(text).get("skill_keys", [])
    except (json.JSONDecodeError, AttributeError):
        selected_keys = []

    activated_keys: list[str] = []
    xml_parts: list[str] = []
    skill_infos: list[dict] = []

    for key in selected_keys:
        data = await SkillRegistry.get_skill(key)
        if not data:
            continue
        activated_keys.append(key)
        xml_parts.append(data.get("skill_xml_body", ""))
        skill_infos.append({
            "display_name": _get_display_name(key, catalog),
            "skill_desc": data.get("skill_desc", ""),
        })

    if len(xml_parts) == 1:
        skill_context = xml_parts[0]
    elif len(xml_parts) > 1:
        skill_context = _compile_skills(xml_parts)
    else:
        skill_context = ""

    return {
        "skill_context": skill_context,
        "activated_skill_keys": activated_keys,
        "_activated_skill_infos": skill_infos,
    }


def _get_display_name(skill_key: str, catalog: list[dict]) -> str:
    for c in catalog:
        if c.get("skill_key") == skill_key:
            return c.get("display_name", skill_key) if "display_name" in c else skill_key
    return skill_key


# ---- 节点 3：Planner（思考决策） ----

async def planner_node(state: ReportState, config: RunnableConfig) -> dict:
    brief_obs = [
        {"tool": o.get("tool", "unknown"), "summary": str(o.get("result", ""))[:300]}
        for o in state.get("observations", [])
    ]
    obs_text = json.dumps(brief_obs, ensure_ascii=False)
    loop = state.get("react_loop_count", 0)
    enable_search = config["configurable"].get("enable_search", False)

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

    skill_context = state.get("skill_context", "")

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
        "current_action": plan.get("action"),
        "current_action_input": plan.get("action_input", {}),
        "is_finish": plan.get("finish", False) or loop + 1 >= _MAX_LOOPS,
        "react_loop_count": loop + 1,
    }


# ---- 节点 5：工具执行 ----

async def tool_executor_node(state: ReportState, config: RunnableConfig) -> dict:
    tool_name = state.get("current_action", "") or ""
    tool_args = state.get("current_action_input", {}) or {}

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
            "react_loop_count": state.get("react_loop_count", 0),
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


# ---- 节点 6：观察记录 ----

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


# ---- 节点 7：报告生成 ----

async def report_generator_node(state: ReportState, config: RunnableConfig) -> dict:
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

    sub.add_node("collect_task",     collect_task_node)
    sub.add_node("activate_skill",   activate_skill_node)
    sub.add_node("planner",          planner_node)
    sub.add_node("tool_executor",    tool_executor_node)
    sub.add_node("observation",      observation_node)
    sub.add_node("report_generator", report_generator_node)

    sub.set_entry_point("collect_task")
    sub.add_edge("collect_task",    "activate_skill")
    sub.add_edge("activate_skill",  "planner")

    sub.add_conditional_edges("planner", route_after_planner, {
        "tool_executor":    "tool_executor",
        "report_generator": "report_generator",
    })

    sub.add_edge("tool_executor", "observation")
    sub.add_edge("observation",   "planner")
    sub.add_edge("report_generator", END)

    return sub.compile()
