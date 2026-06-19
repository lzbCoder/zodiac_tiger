from langchain_core.tools import tool
from langchain_tavily._utilities import TavilySearchAPIWrapper
from app.config import settings

# 直接使用底层 HTTP 包装器，绕过 LangChain BaseTool 回调体系，
# 避免 on_tool_start/end 事件被重复触发（TavilySearch 本身是 BaseTool，
# .invoke() 会产生额外的工具调用事件）。
_api = TavilySearchAPIWrapper(tavily_api_key=settings.TAVILY_API_KEY)


@tool
def web_search(query: str) -> str:
    """搜索互联网获取最新信息。当需要实时数据、新闻、日期或不确定的知识时使用。"""
    result = _api.raw_results(
        query=query,
        max_results=5,
        search_depth="basic",
        include_domains=None,
        exclude_domains=None,
        include_answer=None,
        include_raw_content=None,
        include_images=None,
        include_image_descriptions=None,
        include_favicon=None,
        topic=None,
        time_range=None,
        country=None,
        auto_parameters=None,
        start_date=None,
        end_date=None,
        include_usage=None,
        exact_match=None,
    )
    items = result.get("results", [])
    if not items:
        return str(result)
    return "\n\n".join(
        f"[{r['title']}]({r['url']})\n{r.get('content', '')[:800]}"
        for r in items[:5]
    )
