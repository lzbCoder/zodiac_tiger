"""联网搜索执行：搜索一次，结果注入新 prompt 生成最终回复。"""

from langchain_core.messages import HumanMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.tools.web_search import web_search

_SEARCH_TOOLS = [convert_to_openai_tool(web_search)]
_TOOL_MAP = {web_search.name: web_search}


async def _execute_search(query: str) -> str:
    """执行一次 Tavily 搜索，返回格式化结果。"""
    try:
        result = web_search.invoke({"query": query})
        if isinstance(result, dict) and result.get("results"):
            return "\n\n".join(
                f"[{r['title']}]({r.get('url','')})\n{r.get('content','')[:300]}"
                for r in result["results"][:8]
            )
        return str(result)
    except Exception as e:
        return f"搜索失败: {e}"


async def run_with_tools(llm, prompt: str) -> str:
    """调用 LLM（带 tools），若 LLM 调用了搜索，则用搜索结果重新生成。"""
    resp = await llm.ainvoke(prompt, tools=_SEARCH_TOOLS)

    tc_list = getattr(resp, "tool_calls", None)
    if not tc_list:
        return resp.content or ""

    # 合并所有搜索 query，执行一次搜索
    queries = [tc.get("args", {}).get("query", "") for tc in tc_list]
    combined_query = " ".join(q for q in queries if q)
    search_result = await _execute_search(combined_query)

    # 用新 prompt + 搜索结果重新生成，不传对话历史（避免 LLM 陷入 tool-call 循环）
    final_prompt = (
        "请基于以下搜索结果回答用户问题。\n\n"
        f"用户问题：{prompt}\n\n"
        f"搜索结果：\n{search_result}\n\n"
        "请直接生成完整的最终答案。"
    )
    resp = await llm.ainvoke(final_prompt)
    return resp.content or ""
