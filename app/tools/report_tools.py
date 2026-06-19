"""数据分析专用工具集"""

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


REPORT_TOOLS = [query_sql, read_excel, web_search]
