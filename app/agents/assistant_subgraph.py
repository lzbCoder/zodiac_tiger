"""综合助手 ReAct 子图（原生 Function Calling）：思考 → 并行工具 → ToolMessage 回填 → 循环 → 回答生成"""

import json
import asyncio
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import HumanMessage, ToolMessage
from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.db.session import get_db_session
from app.factory.llm_factory import create_llm
from app.mcp.mcp_manager import GlobalMcpManager
from app.models.agent_skill_rel import AgentSkillRel
from app.prompts.loader import render, render_messages
from app.skills.registry import SkillRegistry
from app.state.assistant_state import AssistantState
from app.tools.web_search import web_search as web_search_tool
from app.agents.agent_utils import astream_accumulate, astream_tool_call
from app.agents.error_policy import (
    DEFAULT_RETRY, NO_RETRY, log_and_raise, DEFAULT_TIMEOUT, LONG_TIMEOUT,
)

_MAX_LOOPS = 10


def _compile_skills(xml_bodies: list[str]) -> str:
    """将多个技能 XML 合并为统一内容（代码合并，不调用 LLM）。"""
    return "\n\n".join(xml_bodies)


async def _collect_tools(enable_search: bool) -> dict:
    """合并静态工具（web_search）与 MCP 动态工具，返回 name → tool 映射。"""
    static_tools = {web_search_tool.name: web_search_tool} if enable_search else {}
    try:
        mcp_tools = {t.name: t for t in await GlobalMcpManager.build_tools_for_agent("assistant_agent")}
    except Exception:
        mcp_tools = {}
    return {**static_tools, **mcp_tools}


# ---- 节点 1：任务收集 ----

async def collect_task_node(state: AssistantState, config: RunnableConfig) -> dict:
    user_msg = ""
    for m in reversed(state.get("messages", [])):
        if hasattr(m, "type") and m.type == "human":
            user_msg = m.content
            break
    return {
        "task": user_msg,
        "scratchpad": [HumanMessage(content=user_msg)],  # 每轮对话重置 ReAct 草稿
        "react_loop_count": 0,
        "skill_context": "",
        "activated_skill_keys": [],
    }


# ---- 节点 2：Triage（任务复杂度判定，一次性节点，保持单字符串） ----

async def triage_node(state: AssistantState, config: RunnableConfig) -> dict:
    """任务分析：单次 LLM 调用判定任务复杂度（simple / complex）。流式展示为「思考」条目。"""
    task = state.get("task", "")
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


# ---- 节点 2.5：技能激活（一次性节点，保持单字符串） ----

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

    triage_summary = f"任务复杂度：{state.get('complexity', 'simple')}"
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


# ---- 节点 3：Planner（原生 FC 决策，流式推理） ----

async def planner_node(state: AssistantState, config: RunnableConfig) -> dict:
    enable_search = config["configurable"].get("enable_search", False)
    loop = state.get("react_loop_count", 0)
    scratchpad = list(state.get("scratchpad", []))

    tool_map = await _collect_tools(enable_search)

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    if tool_map:
        llm = llm.bind_tools(list(tool_map.values()))

    system_msgs = render_messages(
        "assistant_planner",
        task=state.get("task", ""),
        skill_context=state.get("skill_context", ""),
        loop=loop,
        max_loops=_MAX_LOOPS,
    )
    ai = await astream_tool_call(llm, system_msgs + scratchpad)

    return {
        "scratchpad": scratchpad + [ai],
        "react_loop_count": loop + 1,
    }


# ---- 节点 4：工具执行（并行 + ToolMessage 回填） ----

async def tool_executor_node(state: AssistantState, config: RunnableConfig) -> dict:
    enable_search = config["configurable"].get("enable_search", False)
    scratchpad = list(state.get("scratchpad", []))
    last = scratchpad[-1] if scratchpad else None
    tool_calls = getattr(last, "tool_calls", None) or []
    if not tool_calls:
        return {"scratchpad": scratchpad}

    tool_map = await _collect_tools(enable_search)

    async def _run_one(tc: dict) -> ToolMessage:
        name = tc.get("name", "")
        tool = tool_map.get(name)
        call_id = tc.get("id", "")
        if not tool:
            logger.warning(f"[assistant tool_executor] 无效工具: {name}")
            return ToolMessage(content=f"未知工具: {name}", tool_call_id=call_id, name=name)
        try:
            result = await tool.ainvoke(tc.get("args", {}))
            return ToolMessage(content=str(result), tool_call_id=call_id, name=name)
        except Exception as e:
            return ToolMessage(content=f"工具执行失败: {e}", tool_call_id=call_id, name=name)

    tool_messages = await asyncio.gather(*[_run_one(tc) for tc in tool_calls])
    return {"scratchpad": scratchpad + list(tool_messages)}


# ---- 节点 5：回答生成 ----

def _scratchpad_observations(scratchpad: list) -> str:
    """从 scratchpad 的 ToolMessage 汇总工具结果，供回答生成。"""
    sections = [
        f"【{getattr(m, 'name', 'tool')}】\n{m.content}"
        for m in scratchpad if isinstance(m, ToolMessage)
    ]
    return "\n\n".join(sections) if sections else "无"


async def answer_generator_node(state: AssistantState, config: RunnableConfig) -> dict:
    obs_text = _scratchpad_observations(state.get("scratchpad", []))
    messages = render_messages("assistant_answer", task=state.get("task", ""), obs_text=obs_text)

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    answer = ""
    async for chunk in llm.astream(messages):
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
    """planner 决策后：有 tool_calls 且未超上限 → 执行工具；否则 → 生成回答。"""
    scratchpad = state.get("scratchpad", [])
    last = scratchpad[-1] if scratchpad else None
    has_tool_calls = bool(getattr(last, "tool_calls", None))
    if has_tool_calls and state.get("react_loop_count", 0) < _MAX_LOOPS:
        return "assistant_tool_executor"
    return "assistant_answer_generator"


# ---- 构建子图 ----

def build_assistant_agent() -> StateGraph:
    sub = StateGraph(AssistantState)
    sub.set_node_defaults(
        retry_policy=DEFAULT_RETRY, error_handler=log_and_raise, timeout=DEFAULT_TIMEOUT)

    sub.add_node("assistant_collect_task",     collect_task_node)
    sub.add_node("assistant_triage",           triage_node)
    sub.add_node("assistant_activate_skill",   activate_skill_node)
    sub.add_node("assistant_planner",          planner_node)
    sub.add_node("assistant_tool_executor",    tool_executor_node, retry_policy=NO_RETRY)  # 调工具，不重试整节点
    sub.add_node("assistant_answer_generator", answer_generator_node, timeout=LONG_TIMEOUT)

    sub.set_entry_point("assistant_collect_task")
    sub.add_edge("assistant_collect_task",   "assistant_triage")
    sub.add_edge("assistant_triage",         "assistant_activate_skill")
    sub.add_edge("assistant_activate_skill", "assistant_planner")

    sub.add_conditional_edges("assistant_planner", route_after_planner, {
        "assistant_tool_executor":    "assistant_tool_executor",
        "assistant_answer_generator": "assistant_answer_generator",
    })

    sub.add_edge("assistant_tool_executor", "assistant_planner")  # ToolMessage 回填后回到 planner
    sub.add_edge("assistant_answer_generator", END)

    return sub.compile()
