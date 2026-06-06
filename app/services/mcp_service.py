from sqlalchemy import select, update, delete

from app.db.session import get_db_session
from app.models.mcp_config import McpConfig
from app.models.mcp_call_log import McpCallLog
from app.mcp.gateway import McpGateway
from app.mcp.client import McpClient


async def get_mcp_list() -> list[dict]:
    async with get_db_session() as session:
        stmt = select(McpConfig).order_by(McpConfig.id)
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "url": r.url,
            "auth_type": r.auth_type,
            "api_key": r.api_key[:8] + "****" if r.api_key and len(r.api_key) > 8 else r.api_key,
            "timeout": r.timeout,
            "status": r.status,
            "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
        }
        for r in rows
    ]


async def save_mcp(data: dict) -> dict:
    async with get_db_session() as session:
        if data.get("id"):
            stmt = (
                update(McpConfig)
                .where(McpConfig.id == data["id"])
                .values(
                    name=data["name"],
                    url=data["url"],
                    auth_type=data.get("auth_type", "none"),
                    api_key=data.get("api_key"),
                    timeout=data.get("timeout", 20),
                    status=data.get("status", 1),
                )
            )
            await session.execute(stmt)
            mcp_id = data["id"]
        else:
            obj = McpConfig(
                name=data["name"],
                url=data["url"],
                auth_type=data.get("auth_type", "none"),
                api_key=data.get("api_key"),
                timeout=data.get("timeout", 20),
                status=data.get("status", 1),
            )
            session.add(obj)
            await session.flush()
            mcp_id = obj.id
        await session.commit()
    await McpGateway.refresh()
    return {"id": mcp_id}


async def test_mcp(mcp_id: int) -> dict:
    async with get_db_session() as session:
        stmt = select(McpConfig.url, McpConfig.auth_type, McpConfig.api_key, McpConfig.timeout).where(
            McpConfig.id == mcp_id
        )
        result = await session.execute(stmt)
        r = result.first()
    if not r:
        return {"success": False, "message": "MCP 服务不存在"}

    ok = await McpClient.test(
        url=r.url,
        auth_type=r.auth_type,
        api_key=r.api_key,
        timeout=r.timeout,
    )
    return {"success": ok, "message": "连接成功" if ok else "连接失败"}


async def toggle_mcp_status(mcp_id: int, status: int) -> None:
    async with get_db_session() as session:
        stmt = update(McpConfig).where(McpConfig.id == mcp_id).values(status=status)
        await session.execute(stmt)
        await session.commit()
    await McpGateway.refresh()


async def delete_mcp(mcp_id: int) -> None:
    async with get_db_session() as session:
        stmt = delete(McpConfig).where(McpConfig.id == mcp_id)
        await session.execute(stmt)
        await session.commit()
    await McpGateway.refresh()


async def get_mcp_logs(mcp_id: int, limit: int = 50) -> list[dict]:
    async with get_db_session() as session:
        stmt = (
            select(McpCallLog.id, McpCallLog.service_name, McpCallLog.status, McpCallLog.result, McpCallLog.create_time)
            .where(McpCallLog.mcp_id == mcp_id)
            .order_by(McpCallLog.create_time.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.all()
    return [
        {
            "id": r.id,
            "service_name": r.service_name,
            "status": r.status,
            "result": r.result,
            "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
        }
        for r in rows
    ]
