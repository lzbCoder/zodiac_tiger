"""MCP 工具白名单管理"""

from sqlalchemy import select, update

from app.db.session import get_db_session
from app.models.mcp_tool_info import McpToolInfo


async def get_tools_by_mcp(mcp_key: str) -> list[dict]:
    """获取指定 MCP 服务的全部工具（含白名单状态）。"""
    async with get_db_session() as session:
        rows = (await session.execute(
            select(McpToolInfo)
            .where(McpToolInfo.mcp_key == mcp_key)
            .order_by(McpToolInfo.id)
        )).scalars().all()
    return [_row_to_dict(r) for r in rows]


async def get_allowed_tools(mcp_key: str) -> list[dict]:
    """获取 is_allow=1 的工具，供 GlobalMcpManager 动态构建工具集。"""
    async with get_db_session() as session:
        rows = (await session.execute(
            select(McpToolInfo)
            .where(McpToolInfo.mcp_key == mcp_key, McpToolInfo.is_allow == 1)
        )).scalars().all()
    return [_row_to_dict(r) for r in rows]


async def toggle_tool_allow(mcp_key: str, tool_name: str, is_allow: int):
    """修改单个工具的白名单开关。"""
    async with get_db_session() as session:
        await session.execute(
            update(McpToolInfo)
            .where(McpToolInfo.mcp_key == mcp_key, McpToolInfo.tool_name == tool_name)
            .values(is_allow=is_allow)
        )
        await session.commit()


def _row_to_dict(r: McpToolInfo) -> dict:
    return {
        "mcp_key": r.mcp_key,
        "tool_name": r.tool_name,
        "tool_desc": r.tool_desc,
        "input_schema": r.input_schema,
        "is_allow": r.is_allow,
    }
