"""数据分析 ReAct 子图：思考 → 行动 → 观察 → 循环 → 报告生成"""

import json
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.state.report_state import ReportState
from app.tools.report_tools import REPORT_TOOLS

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

    obs_text = json.dumps(state.get("observations", []), ensure_ascii=False, indent=2)
    loop = state.get("react_loop_count", 0)
    enable_search = config["configurable"].get("enable_search", False)

    search_tool_desc = "\n- tavily_search_report：联网搜索，参数 {\"query\": \"搜索词\"}" if enable_search else ""

    prompt = f"""你是资深数据分析师。当前任务：{state.get('task', '')}

历史观察结果：{obs_text if obs_text != '[]' else '无'}
已执行循环次数：{loop} / {_MAX_LOOPS}

请分析并决定下一步行动。返回纯 JSON：

{{"thought": "你的分析思考", "action": "工具名或null", "action_input": {{}}, "finish": true/false}}

可用工具：
- query_sql：执行SQL查询，参数 {{"sql": "SELECT..."}}
- read_excel：读取Excel文件，参数 {{"file_path": "路径"}}
- generate_chart：生成图表，参数 {{"data_desc": "描述", "chart_type": "bar/line/pie"}}{search_tool_desc}

规则：
1. 信息不足 → 调用工具查询
2. 信息足够 → finish=true, action=null
3. 超过 {_MAX_LOOPS} 轮强制结束
4. 只返回 JSON，不要其他内容
提示：如需从多个维度展示数据，可多次调用 generate_chart 生成不同类型（bar/pie）的图表。"""

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
    # 直接读取 planner 保存的工具调用信息，无需再次调用 LLM 提取
    tool_name = state.get("current_action", "") or ""
    tool_args = state.get("current_action_input", {}) or {}
    tool = _TOOL_MAP.get(tool_name)

    if not tool:
        logger.warning(f"[tool_executor] 无效工具: {tool_name}")
        return {
            "observation_result": f"未知工具: {tool_name}",
            "tool_calls": [],
            "react_loop_count": state.get("react_loop_count", 0)
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

async def observation_node(state: ReportState, config: RunnableConfig) -> dict:
    tcs = state.get("tool_calls", [])
    result = state.get("observation_result", "")
    if not tcs or not result:
        return {"react_loop_count": state.get("react_loop_count", 0)}
    tc = tcs[-1]
    obs = {
        "tool": tc.get("name", "unknown"),
        "args": tc.get("args", {}),
        "result": result[:800],
    }
    observations = list(state.get("observations", [])) + [obs]

    # 单独提取图表配置
    charts: list[dict] = []
    if tc.get("name") == "generate_chart" and isinstance(result, str):
        try:
            data = json.loads(result)
            if isinstance(data, dict) and data.get("option"):
                data["option"]["_data_desc"] = tc.get("args", {}).get("data_desc", "")
                charts.append(data["option"])
        except (json.JSONDecodeError, TypeError):
            pass

    existing_charts = list(state.get("charts", []))
    existing_charts.extend(charts)

    return {
        "observations": observations,
        "react_loop_count": state.get("react_loop_count", 0),
        "charts": existing_charts if charts else state.get("charts", []),
    }


# ---- 节点 5：报告生成 ----

async def report_generator_node(state: ReportState, config: RunnableConfig) -> dict:
    from app.factory.llm_factory import create_llm
    from app.config import settings

    # 对工具结果做摘要，chart JSON 替换为文字描述（避免 LLM 将 JSON 写入报告正文）
    raw_obs = state.get("observations", [])
    summarized_obs = []
    for o in raw_obs:
        if o.get("tool") == "generate_chart":
            data_desc = o.get("args", {}).get("data_desc", "")
            chart_type = o.get("args", {}).get("chart_type", "bar")
            summarized_obs.append(f"[图表] {chart_type}图：{data_desc}")
        else:
            summarized_obs.append(f"{o.get('tool')}: {str(o.get('result', ''))[:200]}")
    obs_text = json.dumps(summarized_obs, ensure_ascii=False, indent=2)
    has_charts = bool(state.get("charts", []))
    chart_section = "\n## 图表展示" if has_charts else ""
    rule = "\n\n规则：'图表展示'章节只写标题，不要写任何正文或说明，图表由系统自动渲染。不考虑图表内容。" if has_charts else "\n\n注意：不要输出任何图表相关章节（如图表展示、数据可视化等），本报告仅文字分析。"
    prompt = f"""你是资深数据分析师，请根据以下分析过程生成专业数据分析报告。

任务：{state.get('task', '')}

分析过程与数据：{obs_text}

严格按以下格式输出，不要增加或减少章节：
## 分析摘要
## 关键发现
## 数据详情{chart_section}
## 行动建议{rule}

请用 Markdown 输出。"""

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    resp = await llm.ainvoke(prompt)
    return {
        "final_report": resp.content,
        "generate_content": resp.content,
        "generate_format": state.get("generate_format", ""),
        "messages": [{"role": "ai", "content": resp.content}],
        "charts": state.get("charts", []),
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
