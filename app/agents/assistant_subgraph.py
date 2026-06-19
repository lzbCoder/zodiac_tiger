"""综合助手 ReAct 子图：思考 → 行动 → 观察 → 循环 → 回答生成"""

import json
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.db.session import get_db_session
from app.factory.llm_factory import create_llm
from app.models.agent_skill_rel import AgentSkillRel
from app.prompts.loader import render
from app.skills.registry import SkillRegistry
from app.state.assistant_state import AssistantState
from app.tools.web_search import web_search as web_search_tool
from app.agents.agent_utils import build_tool_desc_section, astream_accumulate

_MAX_LOOPS = 10


def _compile_skills(xml_bodies: list[str]) -> str:
    """将多个技能 XML 合并为统一内容（代码合并，不调用 LLM）。"""
    return "\n\n".join(xml_bodies)


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
        "skill_context": "",
        "activated_skill_keys": [],
    }


# ---- 节点 2：Triage（分流 + 规划） ----

async def triage_node(state: AssistantState, config: RunnableConfig) -> dict:
    """任务分析：单次 LLM 调用判定任务复杂度（simple / complex），后续统一走 ReAct 循环。

    流式调用，思考过程实时展示为「LLM 思考」附属条目。
    """
    task = state.get("task", "")
    # 启发式：非常简短的问题（<20 字）直接视为简单任务，跳过 LLM 调用
    if len(task) < 20:
        return {"complexity": "simple"}

    prompt = render("assistant_triage", task=task)
    llm = create_llm(settings.INTENT_MODEL, streaming=True)
    content = await astream_accumulate(llm, prompt)
    try:
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        complexity = json.loads(text).get("complexity", "simple")
    except (json.JSONDecodeError, IndexError):
        logger.warning(f"[assistant] Triage JSON 解析失败: {content[:200]}")
        complexity = "simple"

    return {"complexity": complexity if complexity == "complex" else "simple"}


# ---- 节点 2.5：技能激活 ----

async def activate_skill_node(state: AssistantState, config: RunnableConfig) -> dict:
    """查询助手 Agent 绑定的技能目录，LLM 结合任务和 triage 结果筛选，从 Redis 获取激活内容。"""
    async with get_db_session() as session:
        rows = (await session.execute(
            select(AgentSkillRel.skill_key, AgentSkillRel.skill_desc)
            .where(AgentSkillRel.agent_code == "assistant_agent")
        )).all()

    if not rows:
        return {"skill_context": "", "activated_skill_keys": [], "_activated_skill_infos": []}

    catalog = [{"skill_key": r.skill_key, "skill_desc": r.skill_desc or ""} for r in rows]
    catalog_text = "\n".join(f"- {c['skill_key']}：{c['skill_desc']}" for c in catalog)

    complexity = state.get("complexity", "simple")
    triage_summary = f"任务复杂度：{complexity}"

    prompt = render(
        "skill_activate",
        task=state.get("task", ""),
        catalog_text=catalog_text,
        triage_summary=triage_summary,
    )
    llm = create_llm(settings.INTENT_MODEL, streaming=True)
    content = await astream_accumulate(llm, prompt)
    try:
        text = content.strip()
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
            "display_name": key,
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


# ---- Planner 私有辅助函数 ----

async def _collect_agent_tools(enable_search: bool) -> dict:
    """合并静态工具与 MCP 动态工具，构建完整工具映射表。"""
    from app.mcp.mcp_manager import GlobalMcpManager
    static_tools = _build_tool_map(enable_search)
    try:
        mcp_tools = {t.name: t for t in await GlobalMcpManager.build_tools_for_agent("assistant_agent")}
    except Exception:
        mcp_tools = {}
    return {**static_tools, **mcp_tools}


def _build_obs_text(observations: list) -> str:
    """将观察列表压缩为 JSON 字符串，每条结果截断至 300 字符，避免 prompt 过长。"""
    brief = [
        {"tool": o.get("tool", "unknown"), "summary": str(o.get("result", ""))[:300]}
        for o in observations
    ]
    return json.dumps(brief, ensure_ascii=False)


# ---- 节点 3：Planner（思考决策） ----

async def planner_node(state: AssistantState, config: RunnableConfig) -> dict:
    enable_search = config["configurable"].get("enable_search", False)
    loop = state.get("react_loop_count", 0)

    # 构建工具集、技能上下文、观察摘要
    all_tools = await _collect_agent_tools(enable_search)
    skill_context = state.get("skill_context", "")
    obs_text = _build_obs_text(state.get("observations", []))

    tool_section = build_tool_desc_section(all_tools)
    no_tool_note = "\n注意：当前无可用工具，请直接基于知识回答，将 finish 设为 true。" if not all_tools else ""

    # 调用 Planner LLM，获取思考/行动决策（流式，思考过程实时展示）
    prompt = render(
        "assistant_planner",
        task=state.get("task", ""),
        skill_context=skill_context,
        plan_section="",
        obs_text=obs_text if obs_text != "[]" else "无",
        loop=loop,
        max_loops=_MAX_LOOPS,
        plan_json_fields="",
        tool_section=tool_section,
        no_tool_note=no_tool_note,
        plan_rules="",
    )
    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    content = await astream_accumulate(llm, prompt)
    try:
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        decision = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"[assistant] Planner JSON 解析失败: {content[:200]}")
        decision = {"thought": "无法解析思考结果，直接生成回答", "action": None, "action_input": {}, "finish": True}

    new_loop = loop + 1
    return {
        "current_thought": decision.get("thought", ""),
        "current_action": decision.get("action"),
        "current_action_input": decision.get("action_input", {}),
        "react_loop_count": new_loop,
        "is_finish": decision.get("finish", False) or new_loop >= _MAX_LOOPS,
    }


# ---- 节点 4：工具执行 ----

async def tool_executor_node(state: AssistantState, config: RunnableConfig) -> dict:
    enable_search = config["configurable"].get("enable_search", False)
    tool_map = await _collect_agent_tools(enable_search)

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
        # MCP 工具为 async，优先使用 ainvoke；本地工具兼容 invoke
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


# ---- 节点 5：观察记录 ----

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


# ---- 节点 6：回答生成 ----

async def answer_generator_node(state: AssistantState, config: RunnableConfig) -> dict:
    raw_obs = state.get("observations", [])
    obs_sections = []
    for o in raw_obs:
        obs_sections.append(f"【{o.get('tool', 'unknown')}】\n{str(o.get('result', ''))}")
    obs_text = "\n\n".join(obs_sections) if obs_sections else "无"

    prompt = render("assistant_answer", task=state.get("task", ""), obs_text=obs_text)

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    answer = ""
    async for chunk in llm.astream(prompt):
        if chunk.content:
            answer += chunk.content

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

def build_assistant_agent() -> StateGraph:
    sub = StateGraph(AssistantState)

    sub.add_node("assistant_collect_task",     collect_task_node)
    sub.add_node("assistant_triage",           triage_node)
    sub.add_node("assistant_activate_skill",   activate_skill_node)
    sub.add_node("assistant_planner",          planner_node)
    sub.add_node("assistant_tool_executor",    tool_executor_node)
    sub.add_node("assistant_observation",      observation_node)
    sub.add_node("assistant_answer_generator", answer_generator_node)

    sub.set_entry_point("assistant_collect_task")
    sub.add_edge("assistant_collect_task",   "assistant_triage")
    sub.add_edge("assistant_triage",         "assistant_activate_skill")
    sub.add_edge("assistant_activate_skill", "assistant_planner")

    sub.add_conditional_edges("assistant_planner", route_after_planner, {
        "assistant_tool_executor":    "assistant_tool_executor",
        "assistant_answer_generator": "assistant_answer_generator",
    })

    sub.add_edge("assistant_tool_executor", "assistant_observation")
    sub.add_edge("assistant_observation",   "assistant_planner")
    sub.add_edge("assistant_answer_generator", END)

    return sub.compile()
