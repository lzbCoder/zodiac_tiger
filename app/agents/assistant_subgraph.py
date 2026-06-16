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

    prompt = f"""你是任务分流助手。判断下面用户任务是「简单」还是「复杂」，并在复杂时拆解为有序步骤。

用户任务：{task}

判定标准（请严格遵循）：
- simple：单一、直接的请求（问答、闲聊、简短文案、单步操作），无需多步骤即可完成。
- complex：需要多步骤协作的请求（如先调研再对比再总结、多来源汇总、含先后依赖的任务）。
- 注意：不清楚用户需要多步骤时，默认 simple，不要过度拆解。

返回纯 JSON：
{{"complexity": "simple"或"complex", "plan": ["步骤1", "步骤2", ...]}}

规则：
1. simple 时 plan 返回空数组 []。
2. complex 时 plan 给出 2~6 个简洁、可执行的步骤描述（每条不超过 15 字，**必须是对用户有意义的描述**）。
3. 只返回 JSON，不要其他内容。"""

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

    if all_tools:
        tool_desc_lines = "\n".join(
            f"- {name}：{t.description}，参数格式见工具定义"
            for name, t in all_tools.items()
        )
        tool_section = f"\n可用工具：\n{tool_desc_lines}"
        no_tool_note = ""
    else:
        tool_section = "\n可用工具：（无）"
        no_tool_note = "\n注意：当前无可用工具，请直接基于知识回答，将 finish 设为 true。"

    prompt = f"""你是越群山综合智能助手，负责处理报表、旅游之外的各类任务。当前任务：{state.get('task', '')}{skill_context}{plan_section}

历史观察结果：{obs_text if obs_text != '[]' else '无'}
已执行循环次数：{loop} / {_MAX_LOOPS}

请分析并决定下一步行动。返回纯 JSON：

{{"thought": "你的分析思考", "action": "工具名或null", "action_input": {{}}, "finish": true/false{plan_json_fields}}}
{tool_section}{no_tool_note}

规则：
1. 需要实时信息、最新数据 → 调用对应工具
2. 信息足够或无工具可用 → finish=true, action=null
3. 超过 {_MAX_LOOPS} 轮强制结束
4. 只返回 JSON，不要其他内容{plan_rules}"""

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
