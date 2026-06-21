"""Travel SubGraph：参数提取 → 校验 → 中断 → 高德查询 → LLM 生成行程"""

import json
from langgraph.graph import StateGraph, END
from langchain_core.runnables import RunnableConfig
from loguru import logger

from app.config import settings
from app.factory.llm_factory import create_llm
from app.state.travel_state import TravelState
from app.prompts.loader import render
from app.agents.agent_utils import astream_accumulate
from app.agents.error_policy import (
    DEFAULT_RETRY, NO_RETRY, log_and_raise, DEFAULT_TIMEOUT, LONG_TIMEOUT,
)

# 必填参数
_REQUIRED = ["traveler_count", "budget", "days", "origin", "destination"]
_LABELS = {
    "traveler_count": "出行人数",
    "budget": "总预算(元)",
    "days": "出行天数",
    "origin": "出发城市",
    "destination": "目的城市",
}


# ---- 节点：参数提取 ----

async def collect_params_node(state: TravelState, config: RunnableConfig) -> dict:
    """LLM 从用户消息中结构化提取旅游参数（流式，思考过程实时展示）。"""
    user_msg = ""
    for m in reversed(state.get("messages", [])):
        if hasattr(m, "type") and m.type == "human":
            user_msg = m.content
            break

    llm = create_llm(settings.INTENT_MODEL, streaming=True)
    prompt = render("travel_collect_params", user_msg=user_msg)
    content = await astream_accumulate(llm, prompt)
    try:
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        params = json.loads(text)
    except Exception:
        logger.warning(f"参数提取 JSON 解析失败: {content[:200]}")
        return {}

    result = {}
    for k in _REQUIRED:
        v = params.get(k)
        if v is not None and v != "":
            result[k] = int(v) if k in ("traveler_count", "budget", "days") else str(v)
    return result


# ---- 节点：参数校验 ----

async def validate_params_node(state: TravelState, config: RunnableConfig) -> dict:
    """循环检查必填参数，缺失时逐个中断。内部自闭环，不依赖条件边。

    定义为 async：LangGraph 的节点超时仅支持 async 节点（同步执行无法安全取消）。
    函数体无 await，interrupt() 在 async 节点中同样工作。
    """
    from langgraph.types import interrupt

    updates = {}
    for f in _REQUIRED:
        val = state.get(f)
        # 检查已有 state + 本轮已更新的值
        if val is None and f not in updates:
            result = interrupt({
                "type": "travel_param_missing",
                "field": f,
                "label": _LABELS[f],
                "prompt": f"请输入{_LABELS[f]}",
            })
            if isinstance(result, dict):
                updates.update(result)
            else:
                updates[f] = result
    return updates


# ---- 节点：高德地理编码 ----

async def query_geo_node(state: TravelState, config: RunnableConfig) -> dict:
    destination = state.get("destination", "")
    if not destination:
        return {"geo_info": {"location": "116.397,39.908", "adcode": "110000"}}
    from app.tools.amap_tools import amap_geocode
    result = amap_geocode.invoke({"address": destination})
    return {"geo_info": result}


# ---- 节点：高德天气 ----

async def query_weather_node(state: TravelState, config: RunnableConfig) -> dict:
    geo = state.get("geo_info", {})
    adcode = geo.get("adcode", "110000")
    from app.tools.amap_tools import amap_weather
    result = amap_weather.invoke({"adcode": adcode})
    return {"weather_info": result}


# ---- 节点：高德驾车路线 ----

async def query_route_node(state: TravelState, config: RunnableConfig) -> dict:
    origin = state.get("origin", "")
    destination = state.get("destination", "")
    if not origin or not destination:
        return {"route_info": {"distance_km": 0, "duration_hour": 0}}
    from app.tools.amap_tools import amap_driving_route
    result = amap_driving_route.invoke({"origin": origin, "destination": destination})
    return {"route_info": result}


# ---- 节点：LLM 生成行程 ----

async def generate_plan_node(state: TravelState, config: RunnableConfig) -> dict:
    route = state.get("route_info", {})
    weather = state.get("weather_info", {})

    ctx = {
        "人数": state.get("traveler_count", 1),
        "预算": f"{state.get('budget', 0)}元",
        "天数": state.get("days", 1),
        "出发地": state.get("origin", "未指定"),
        "目的地": state.get("destination", "未指定"),
        "距离": f"{route.get('distance_km', '?')}km",
        "预计耗时": f"{route.get('duration_hour', '?')}h",
        "天气": weather,
    }

    user_msg = ""
    for m in reversed(state.get("messages", [])):
        if hasattr(m, "type") and m.type == "human":
            user_msg = m.content
            break

    llm = create_llm(settings.CHAT_MODEL, streaming=True)
    prompt = render("travel_generate_plan", ctx=str(ctx), user_msg=user_msg)

    enable_search = config["configurable"].get("enable_search", False)
    if enable_search:
        from app.tools.executor import run_with_tools
        prompt += "\n（请使用 web_search 搜索用户问题，搜索结果中的信息优先级高于对话历史。即使对话历史中有不同信息，也必须以搜索结果为准。）"
        resp_content = await run_with_tools(llm, prompt)
    else:
        resp_content = ""
        async for chunk in llm.astream(prompt):
            if chunk.content:
                resp_content += chunk.content

    return {
        "travel_plan": resp_content,
        "generate_content": resp_content,
        "generate_format": state.get("generate_format", ""),
        "messages": [{"role": "ai", "content": resp_content}],
    }


# ---- 构建 SubGraph ----

def _all_params_filled(state: TravelState) -> str:
    for f in _REQUIRED:
        if state.get(f) is None:
            return "validate_params"
    return "query_geo"


def build_travel_subgraph() -> StateGraph:
    sub = StateGraph(TravelState)
    sub.set_node_defaults(
        retry_policy=DEFAULT_RETRY, error_handler=log_and_raise, timeout=DEFAULT_TIMEOUT)

    sub.add_node("collect_params", collect_params_node)
    sub.add_node("validate_params", validate_params_node)
    # 高德查询节点：失败不重试（外部接口由其自身/工具层兜底）
    sub.add_node("query_geo", query_geo_node, retry_policy=NO_RETRY)
    sub.add_node("query_weather", query_weather_node, retry_policy=NO_RETRY)
    sub.add_node("query_route", query_route_node, retry_policy=NO_RETRY)
    sub.add_node("generate_plan", generate_plan_node, timeout=LONG_TIMEOUT)

    sub.set_entry_point("collect_params")
    sub.add_edge("collect_params", "validate_params")
    sub.add_conditional_edges("validate_params", _all_params_filled, {
        "validate_params": "validate_params",
        "query_geo": "query_geo",
    })
    sub.add_edge("query_geo", "query_weather")
    sub.add_edge("query_weather", "query_route")
    sub.add_edge("query_route", "generate_plan")
    sub.add_edge("generate_plan", END)

    return sub.compile()
