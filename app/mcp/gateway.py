import json
from sqlalchemy import select
from loguru import logger

from app.db.redis import get_redis
from app.db.session import get_db_session
from app.models.mcp_config import McpConfig
from app.models.mcp_call_log import McpCallLog
from app.mcp.client import McpClient

MCP_CACHE_KEY = "mcp:all"


class McpGateway:
    """MCP 网关：管理外部服务调用，缓存 MCP 服务列表。"""

    @staticmethod
    async def refresh() -> None:
        """刷新 MCP 服务缓存到 Redis。"""
        async with get_db_session() as session:
            stmt = (
                select(
                    McpConfig.id,
                    McpConfig.name,
                    McpConfig.url,
                    McpConfig.auth_type,
                    McpConfig.api_key,
                    McpConfig.timeout,
                )
                .where(McpConfig.status == 1)
                .order_by(McpConfig.id)
            )
            result = await session.execute(stmt)
            rows = result.all()

        services = [
            {
                "id": r.id,
                "name": r.name,
                "url": r.url,
                "auth_type": r.auth_type,
                "api_key": r.api_key,
                "timeout": r.timeout,
            }
            for r in rows
        ]
        r = await get_redis()
        await r.set(MCP_CACHE_KEY, json.dumps(services, ensure_ascii=False))
        logger.info(f"MCP 缓存已刷新，共 {len(services)} 个可用服务")

    @staticmethod
    async def get_service(mcp_id: int) -> dict | None:
        r = await get_redis()
        data = await r.get(MCP_CACHE_KEY)
        if data is None:
            return None
        services = json.loads(data)
        for s in services:
            if s["id"] == mcp_id:
                return s
        return None

    @staticmethod
    async def call(mcp_id: int, params: dict | None = None, body: dict | None = None) -> dict:
        """通过 MCP 网关调用外部服务。"""
        service = await McpGateway.get_service(mcp_id)
        if service is None:
            raise ValueError(f"MCP 服务不存在或已禁用: id={mcp_id}")

        result = await McpClient.call(
            url=service["url"],
            auth_type=service["auth_type"],
            api_key=service["api_key"],
            timeout=service["timeout"],
            params=params,
            body=body,
        )
        # 记录调用日志
        async with get_db_session() as session:
            session.add(McpCallLog(
                mcp_id=mcp_id,
                service_name=service.get("name", ""),
                status="success",
                result=f"调用 MCP 服务 [{service.get('name', '')}] 成功",
            ))
            await session.commit()
        return result
