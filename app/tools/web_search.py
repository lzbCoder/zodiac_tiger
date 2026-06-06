from langchain_core.tools import tool
from langchain_tavily import TavilySearch
from app.config import settings

_tavily = TavilySearch(
    tavily_api_key=settings.TAVILY_API_KEY,
    max_results=5,
    search_depth="basic",
)


@tool
def web_search(query: str) -> str:
    """搜索互联网获取最新信息。当需要实时数据、新闻、日期或不确定的知识时使用。"""
    result = _tavily.invoke({"query": query})
    if isinstance(result, dict) and result.get("results"):
        return "\n\n".join(
            f"[{r['title']}]({r['url']})\n{r.get('content', '')[:200]}"
            for r in result["results"][:5]
        )
    return str(result)
