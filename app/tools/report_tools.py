"""数据分析专用工具集"""

import json
from langchain_core.tools import tool
from app.tools.web_search import web_search


@tool
def query_sql(sql: str) -> str:
    """执行 SQL 查询业务数据库。输入完整 SQL 语句，返回查询结果。"""
    return f"[SQL 模拟] 已执行: {sql[:200]}\n（请接入真实数据库以获取实际结果）"


@tool
def read_excel(file_path: str) -> str:
    """读取 Excel/CSV 文件，返回表头与数据预览。"""
    import os
    if not os.path.exists(file_path):
        return f"文件不存在: {file_path}"
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True)
        ws = wb.active
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            rows.append(str(row))
            if i >= 20:
                break
        return f"文件: {file_path}\n表: {ws.title}\n预览:\n" + "\n".join(rows)
    except Exception as e:
        return f"读取失败: {e}"


_CATEGORIES = ["品类A", "品类B", "品类C", "品类D"]
_PIE_DATA = [{"name": "品类A", "value": 30}, {"name": "品类B", "value": 50}, {"name": "品类C", "value": 20}]
_BAR_DATA = [{"name": "品类A", "value": 30}, {"name": "品类B", "value": 50}, {"name": "品类C", "value": 20}, {"name": "品类D", "value": 45}]


@tool
def generate_chart(data_desc: str, chart_type: str = "bar") -> str:
    """生成图表配置（bar/line/pie）。返回 ECharts option JSON。"""
    if chart_type == "pie":
        series_data = _PIE_DATA
    else:
        series_data = _BAR_DATA

    series = {"name": data_desc[:20], "type": chart_type, "data": series_data, "label": {"show": True}}
    if chart_type == "pie":
        series["label"]["formatter"] = "{b}: {d}%"

    option = {
        "title": {"text": data_desc[:40]},
        "tooltip": {"trigger": "axis" if chart_type != "pie" else "item"},
        "series": [series],
    }
    if chart_type != "pie":
        option["xAxis"] = {"type": "category", "data": _CATEGORIES}
        option["yAxis"] = {"type": "value"}
    return json.dumps({"option": option}, ensure_ascii=False)


@tool
def tavily_search_report(query: str) -> str:
    """联网搜索行业数据、市场报告、背景信息。"""
    result = web_search.invoke({"query": query})
    if isinstance(result, dict) and result.get("results"):
        return "\n".join(
            f"[{r['title']}]({r.get('url','')})\n{r.get('content','')[:300]}"
            for r in result["results"][:5]
        )
    return str(result)


REPORT_TOOLS = [query_sql, read_excel, generate_chart, tavily_search_report]
