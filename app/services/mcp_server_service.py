"""MCP 服务主配置 CRUD + 连通性检测 + 工具同步"""

from datetime import datetime
from loguru import logger
from sqlalchemy import select, update, delete

from app.db.session import get_db_session
from app.models.mcp_server import McpServerConfig
from app.models.mcp_tool_info import McpToolInfo


async def list_servers() -> list[dict]:
    async with get_db_session() as session:
        rows = (await session.execute(
            select(McpServerConfig).order_by(McpServerConfig.id)
        )).scalars().all()
    return [_row_to_dict(r) for r in rows]


async def save_server(data: dict) -> dict:
    """新增或编辑 MCP 服务配置，完成后刷新全局缓存。"""
    from app.mcp.mcp_manager import GlobalMcpManager

    mcp_key = data["mcp_key"]
    async with get_db_session() as session:
        existing = (await session.execute(
            select(McpServerConfig).where(McpServerConfig.mcp_key == mcp_key)
        )).scalar_one_or_none()

        if existing:
            await session.execute(
                update(McpServerConfig)
                .where(McpServerConfig.mcp_key == mcp_key)
                .values(
                    display_name=data["display_name"],
                    endpoint_url=data["endpoint_url"],
                    auth_headers=data.get("auth_headers", {}),
                    remark=data.get("remark"),
                    update_time=datetime.now(),
                )
            )
        else:
            session.add(McpServerConfig(
                mcp_key=mcp_key,
                display_name=data["display_name"],
                endpoint_url=data["endpoint_url"],
                auth_headers=data.get("auth_headers", {}),
                remark=data.get("remark"),
            ))
        await session.commit()

    GlobalMcpManager.reload(mcp_key, data["endpoint_url"], data.get("auth_headers", {}))
    return {"mcp_key": mcp_key}


async def delete_server(mcp_key: str):
    """级联删除：配置 + 工具清单 + Agent 绑定关系。"""
    from app.mcp.mcp_manager import GlobalMcpManager
    from app.models.agent_mcp_rel import AgentMcpRel

    async with get_db_session() as session:
        await session.execute(delete(McpToolInfo).where(McpToolInfo.mcp_key == mcp_key))
        await session.execute(delete(AgentMcpRel).where(AgentMcpRel.mcp_key == mcp_key))
        await session.execute(delete(McpServerConfig).where(McpServerConfig.mcp_key == mcp_key))
        await session.commit()

    GlobalMcpManager.remove(mcp_key)


async def toggle_enable_status(mcp_key: str, enable_status: int):
    """启用/禁用 MCP 服务，同步刷新缓存。"""
    from app.mcp.mcp_manager import GlobalMcpManager

    async with get_db_session() as session:
        await session.execute(
            update(McpServerConfig)
            .where(McpServerConfig.mcp_key == mcp_key)
            .values(enable_status=enable_status, update_time=datetime.now())
        )
        await session.commit()

    if enable_status == 0:
        GlobalMcpManager.remove(mcp_key)
    else:
        # 重新加载配置到缓存
        async with get_db_session() as session:
            row = (await session.execute(
                select(McpServerConfig).where(McpServerConfig.mcp_key == mcp_key)
            )).scalar_one_or_none()
        if row:
            GlobalMcpManager.reload(mcp_key, row.endpoint_url, row.auth_headers or {})


async def test_connect(endpoint_url: str, auth_headers: dict) -> dict:
    """仅测试连通性，不入库，返回 {ok, message, tool_count}。"""
    from app.mcp.mcp_sdk_client import McpStreamHttpClient
    client = McpStreamHttpClient("_test", endpoint_url, auth_headers)
    ok, msg, tools = await client.test_and_list_tools()
    return {"ok": ok, "message": msg, "tool_count": len(tools)}


async def sync_tools(mcp_key: str) -> int:
    """
    拉取远端工具列表，全量写入 mcp_tool_info（保留已有 is_allow 值），
    同时更新 connect_status 和 last_check_time，返回同步工具数量。
    """
    from app.mcp.mcp_sdk_client import McpStreamHttpClient

    async with get_db_session() as session:
        row = (await session.execute(
            select(McpServerConfig).where(McpServerConfig.mcp_key == mcp_key)
        )).scalar_one_or_none()

    if not row:
        raise ValueError(f"MCP 服务不存在: {mcp_key}")

    client = McpStreamHttpClient(mcp_key, row.endpoint_url, row.auth_headers or {})
    ok, msg, tools = await client.test_and_list_tools()

    connect_status = 1 if ok else 2
    async with get_db_session() as session:
        await session.execute(
            update(McpServerConfig)
            .where(McpServerConfig.mcp_key == mcp_key)
            .values(connect_status=connect_status, last_check_time=datetime.now(), update_time=datetime.now())
        )

        if ok and tools:
            # 读取已有 is_allow 映射
            existing = (await session.execute(
                select(McpToolInfo).where(McpToolInfo.mcp_key == mcp_key)
            )).scalars().all()
            allow_map = {t.tool_name: t.is_allow for t in existing}

            # 全量替换工具记录
            await session.execute(delete(McpToolInfo).where(McpToolInfo.mcp_key == mcp_key))
            for t in tools:
                session.add(McpToolInfo(
                    mcp_key=mcp_key,
                    tool_name=t["tool_name"],
                    tool_desc=t["tool_desc"],
                    input_schema=t["input_schema"],
                    is_allow=allow_map.get(t["tool_name"], 1),  # 保留原 is_allow，新工具默认 1
                ))

        await session.commit()

    if not ok:
        logger.warning(f"MCP [{mcp_key}] 连通失败: {msg}")

    return len(tools)


def _row_to_dict(r: McpServerConfig) -> dict:
    return {
        "mcp_key": r.mcp_key,
        "display_name": r.display_name,
        "endpoint_url": r.endpoint_url,
        "auth_headers": r.auth_headers,
        "enable_status": r.enable_status,
        "connect_status": r.connect_status,
        "last_check_time": r.last_check_time.strftime("%Y-%m-%d %H:%M:%S") if r.last_check_time else None,
        "remark": r.remark,
        "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
    }
