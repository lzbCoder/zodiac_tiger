"""综合助手 ReAct 子图：思考 → 行动 → 观察 → 循环 → 回答生成"""

import json
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.state.assistant_state import AssistantState
from app.tools.web_search import web_search as web_search_tool
from app.agents.agent_utils import build_tool_desc_section
from app.prompts.loader import render

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


# ---- 节点 2：Triage（分流 + 规划） ----

async def triage_node(state: AssistantState, config: RunnableConfig) -> dict:
    """
    单次 LLM 调用完成「复杂度判定 + 步骤规划」：
    - 简单任务：complexity=simple，无计划，后续直接走 ReAct 循环。
    - 复杂任务：complexity=complex，生成显式步骤清单（plan_steps），分步执行并可视化。
    """
    from app.factory.llm_factory import create_llm
    from app.config import settings

    task = state.get("task", "")
    # 启发式：非常简短的问题（<20 字）直接视为简单任务，跳过 LLM 调用
    if len(task) < 20:
        return {"complexity": "simple", "plan_steps": []}

    prompt = render("assistant_triage", task=task)
    llm = create_llm(settings.INTENT_MODEL, streaming=False, tags=["skip_stream"])
    resp = await llm.ainvoke(prompt)
    try:
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        data = json.loads(text)
        complexity = data.get("complexity", "simple")
        plan = data.get("plan", []) or []
    except (json.JSONDecodeError, IndexError):
        logger.warning(f"[assistant] Triage JSON 解析失败: {resp.content[:200]}")
        complexity, plan = "simple", []

    # 如果判定为 simple、无计划、或所有描述都为空，返回简单模式
    if complexity != "complex" or not plan:
        return {"complexity": "simple", "plan_steps": []}

    # 过滤掉空描述，避免前端展示 plan-0、plan-1 等无意义条目
    valid_plan = [str(desc).strip() for desc in plan if str(desc).strip()]
    if not valid_plan:
        return {"complexity": "simple", "plan_steps": []}

    plan_steps = [
        {"index": i, "description": desc, "status": "pending"}
        for i, desc in enumerate(valid_plan)
    ]
    return {"complexity": "complex", "plan_steps": plan_steps}


# ---- 节点 3：Planner（思考决策） ----

async def planner_node(state: AssistantState, config: RunnableConfig) -> dict:
    from app.factory.llm_factory import create_llm
    from app.config import settings
    from app.mcp.mcp_manager import GlobalMcpManager

    enable_search = config["configurable"].get("enable_search", False)
    loop = state.get("react_loop_count", 0)

    # 静态工具 + MCP 动态工具合并
    static_tools = _build_tool_map(enable_search)
    try:
        mcp_tools = {t.name: t for t in await GlobalMcpManager.build_tools_for_agent("assistant_agent")}
    except Exception:
        mcp_tools = {}
    all_tools: dict = {**static_tools, **mcp_tools}

    # 技能系统提示词注入
    from app.skills.manager import GlobalSkillManager
    try:
        skill_list = await GlobalSkillManager.get_skills_for_agent("assistant_agent")
    except Exception:
        skill_list = []
    skill_context = ""
    if skill_list:
        parts = [f"【{s['skill_name']}】\n{s['system_prompt']}"
                 for s in skill_list if s.get("system_prompt")]
        if parts:
            skill_context = "\n\n已加载本地技能（请参考其指令执行）：\n" + "\n\n".join(parts)

    brief_obs = [
        {"tool": o.get("tool", "unknown"), "summary": str(o.get("result", ""))[:300]}
        for o in state.get("observations", [])
    ]
    obs_text = json.dumps(brief_obs, ensure_ascii=False)

    # 计划感知：复杂任务注入步骤清单与当前进度（深拷贝，避免直接改写 state 内字典）
    plan_steps = [dict(s) for s in (state.get("plan_steps") or [])]
    is_complex = bool(plan_steps)
    if is_complex:
        steps_lines = "\n".join(
            f"  {s['index']}. [{s['status']}] {s['description']}" for s in plan_steps
        )
        plan_section = (
            f"\n\n执行计划（共 {len(plan_steps)} 步，按 index 顺序推进）：\n{steps_lines}\n"
            "请严格一次只处理一个步骤。重点聚焦当前处于 [in_progress] 或最早 [pending] 的步骤。"
        )
        plan_json_fields = ', "current_step": 当前步骤index整数, "step_done": true/false'
        plan_rules = (
            "\n5. step_done=true 仅标记当前这一步完成，不要跨步。"
            "\n6. 所有步骤都完成后才设置 finish=true；还有未完成步骤时 finish=false 继续循环。"
        )
    else:
        plan_section = ""
        plan_json_fields = ""
        plan_rules = ""

    tool_section = build_tool_desc_section(all_tools)
    no_tool_note = "\n注意：当前无可用工具，请直接基于知识回答，将 finish 设为 true。" if not all_tools else ""

    prompt = render(
        "assistant_planner",
        task=state.get("task", ""),
        skill_context=skill_context,
        plan_section=plan_section,
        obs_text=obs_text if obs_text != "[]" else "无",
        loop=loop,
        max_loops=_MAX_LOOPS,
        plan_json_fields=plan_json_fields,
        tool_section=tool_section,
        no_tool_note=no_tool_note,
        plan_rules=plan_rules,
    )

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    resp = await llm.ainvoke(prompt)
    try:
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        decision = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"[assistant] Planner JSON 解析失败: {resp.content[:200]}")
        decision = {"thought": "无法解析思考结果，直接生成回答", "action": None, "action_input": {}, "finish": True}

    new_loop = loop + 1
    result: dict = {
        "current_thought": decision.get("thought", ""),
        "current_action": decision.get("action"),
        "current_action_input": decision.get("action_input", {}),
        "react_loop_count": new_loop,
    }

    if is_complex:
        # 确定当前真正的活跃步骤（第一个未完成的步骤）
        active_idx = None
        for s in plan_steps:
            if s["status"] == "in_progress":
                active_idx = s["index"]
                break
            if s["status"] != "done":
                active_idx = s["index"]
                break

        if active_idx is not None:
            step_done = bool(decision.get("step_done", False))
            # 强制：LLM 只能处理当前活跃步骤，不允许跳步
            active = active_idx
            for s in plan_steps:
                if s["index"] < active:
                    s["status"] = "done"
                elif s["index"] == active:
                    s["status"] = "done" if step_done else "in_progress"
                    s["detail"] = decision.get("thought", "")[:1000] if step_done else ""

            all_done = all(s["status"] == "done" for s in plan_steps)
            # 如果当前步骤已完成但后面还有步骤，不让 graph 提前结束
            if step_done and not all_done:
                result["is_finish"] = False
            else:
                result["is_finish"] = all_done or new_loop >= _MAX_LOOPS

            # 确保有一个进行中的步骤
            if not all_done and not any(s["status"] == "in_progress" for s in plan_steps):
                for s in plan_steps:
                    if s["status"] != "done":
                        s["status"] = "in_progress"
                        break
        else:
            all_done = True
            result["is_finish"] = True

        result["plan_steps"] = plan_steps
    else:
        result["is_finish"] = decision.get("finish", False) or new_loop >= _MAX_LOOPS

    return result


# ---- 节点 4：工具执行 ----

async def tool_executor_node(state: AssistantState, config: RunnableConfig) -> dict:
    from app.mcp.mcp_manager import GlobalMcpManager

    enable_search = config["configurable"].get("enable_search", False)
    static_tools = _build_tool_map(enable_search)
    try:
        mcp_tools = {t.name: t for t in await GlobalMcpManager.build_tools_for_agent("assistant_agent")}
    except Exception:
        mcp_tools = {}
    tool_map = {**static_tools, **mcp_tools}

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
    from app.factory.llm_factory import create_llm
    from app.config import settings

    raw_obs = state.get("observations", [])
    obs_sections = []
    for o in raw_obs:
        obs_sections.append(f"【{o.get('tool', 'unknown')}】\n{str(o.get('result', ''))}")
    obs_text = "\n\n".join(obs_sections) if obs_sections else "无"

    prompt = render("assistant_answer", task=state.get("task", ""), obs_text=obs_text)

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

def build_assistant_agent() -> StateGraph:
    sub = StateGraph(AssistantState)

    sub.add_node("assistant_collect_task",     collect_task_node)
    sub.add_node("assistant_triage",           triage_node)
    sub.add_node("assistant_planner",          planner_node)
    sub.add_node("assistant_tool_executor",    tool_executor_node)
    sub.add_node("assistant_observation",      observation_node)
    sub.add_node("assistant_answer_generator", answer_generator_node)

    sub.set_entry_point("assistant_collect_task")
    sub.add_edge("assistant_collect_task", "assistant_triage")
    sub.add_edge("assistant_triage",       "assistant_planner")

    sub.add_conditional_edges("assistant_planner", route_after_planner, {
        "assistant_tool_executor":    "assistant_tool_executor",
        "assistant_answer_generator": "assistant_answer_generator",
    })

    sub.add_edge("assistant_tool_executor", "assistant_observation")
    sub.add_edge("assistant_observation",   "assistant_planner")
    sub.add_edge("assistant_answer_generator", END)

    return sub.compile()
