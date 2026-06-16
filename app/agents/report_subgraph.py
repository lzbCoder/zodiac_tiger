"""数据分析 ReAct 子图：思考 → 行动 → 观察 → 循环 → 报告生成"""

import json
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.state.report_state import ReportState
from app.tools.report_tools import REPORT_TOOLS
from app.agents.agent_utils import build_tool_desc_section

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
        parts = [f"【{s['skill_name']}】\n{s['system_prompt']}"
                 for s in skill_list if s.get("system_prompt")]
        if parts:
            skill_context = "\n\n已加载本地技能（请参考其指令执行）：\n" + "\n\n".join(parts)

    prompt = f"""你是资深数据分析师。当前任务：{state.get('task', '')}{skill_context}

历史观察结果：{obs_text if obs_text != '[]' else '无'}
已执行循环次数：{loop} / {_MAX_LOOPS}

请分析并决定下一步行动。返回纯 JSON：

{{"thought": "你的分析思考", "action": "工具名或null", "action_input": {{}}, "finish": true/false}}
{tool_section}

规则：
1. 信息不足 → 调用工具查询
2. 信息足够 → finish=true, action=null
3. 超过 {_MAX_LOOPS} 轮强制结束
4. 只返回 JSON，不要其他内容
5. 在最终报告中，用 Markdown 表格呈现数据，每张表格都包含表头和数据行
提示：如需从多个维度展示数据，可在报告中用多张表格展示。"""

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
    prompt = f"""你是资深数据分析师，请根据以下分析过程生成专业数据分析报告。

任务：{state.get('task', '')}

分析过程与数据：{obs_text}

## 分析摘要
## 关键发现
## 数据详情
## 行动建议

在「数据详情」章节中，用 Markdown 表格呈现具体数据，每张表格都需要：
- 一个简洁的标题行说明该表内容（如"各品类销售额统计表"）
- 包含表头和数据行
请用 Markdown 输出。
注：不要自行添加"图表展示"、"数据可视化"等标题，系统会自动渲染图表。"""

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    resp = await llm.ainvoke(prompt)
    return {
        "final_report": resp.content,
        "generate_content": resp.content,
        "generate_format": state.get("generate_format", ""),
        "messages": [{"role": "ai", "content": resp.content}],
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
