"""全局 MCP 管理器：Redis 配置缓存 + LangChain 工具转换"""

import json
from typing import Any, Optional, Type

from loguru import logger
from pydantic import BaseModel, Field, create_model
from langchain_core.tools import BaseTool, StructuredTool

_REDIS_KEY = "mcp:configs"


class GlobalMcpManager:
    """
    管理所有远程 MCP 服务的配置缓存（Redis Hash）。
    不维持持久连接——每次工具调用均通过 McpStreamHttpClient 新建短连接。
    Redis Hash 结构：HSET mcp:configs {mcp_key} {json}
    """

    @classmethod
    async def init(cls):
        """启动时从数据库加载所有 enable_status=1 的服务配置，写入 Redis Hash。"""
        from sqlalchemy import select
        from app.db.session import get_db_session
        from app.models.mcp_server import McpServerConfig
        from app.db.redis import get_redis

        async with get_db_session() as session:
            stmt = select(McpServerConfig).where(McpServerConfig.enable_status == 1)
            rows = (await session.execute(stmt)).scalars().all()

        redis = await get_redis()
        await redis.delete(_REDIS_KEY)
        if rows:
            mapping = {
                r.mcp_key: json.dumps(
                    {"endpoint_url": r.endpoint_url, "auth_headers": r.auth_headers or {}},
                    ensure_ascii=False,
                )
                for r in rows
            }
            await redis.hset(_REDIS_KEY, mapping=mapping)

        logger.info(f"MCP Manager 初始化完成，加载 {len(rows)} 个服务")

    @classmethod
    async def reload(cls, mcp_key: str, endpoint_url: str, auth_headers: dict):
        """新增/编辑/启用后刷新单条 Redis 缓存。"""
        from app.db.redis import get_redis

        redis = await get_redis()
        await redis.hset(
            _REDIS_KEY,
            mcp_key,
            json.dumps({"endpoint_url": endpoint_url, "auth_headers": auth_headers}, ensure_ascii=False),
        )

    @classmethod
    async def remove(cls, mcp_key: str):
        """删除/禁用后移除 Redis 缓存条目。"""
        from app.db.redis import get_redis

        redis = await get_redis()
        await redis.hdel(_REDIS_KEY, mcp_key)

    @classmethod
    async def get_client(cls, mcp_key: str):
        """按 mcp_key 从 Redis 读取配置，返回新 McpStreamHttpClient 实例。"""
        from app.mcp.mcp_sdk_client import McpStreamHttpClient
        from app.db.redis import get_redis

        redis = await get_redis()
        raw = await redis.hget(_REDIS_KEY, mcp_key)
        if not raw:
            raise KeyError(f"MCP 配置不存在: {mcp_key}")
        cfg = json.loads(raw)
        return McpStreamHttpClient(
            mcp_key=mcp_key,
            endpoint=cfg["endpoint_url"],
            headers=cfg["auth_headers"],
        )

    @classmethod
    async def build_tools_for_agent(cls, agent_code: str) -> list[BaseTool]:
        """
        根据 agent_code 查询绑定的 MCP 服务及已放行工具，
        返回封装好的 LangChain BaseTool 列表，供 ReAct 子图动态注入。
        """
        from app.db.redis import get_redis
        from app.services.agent_mcp_service import get_mcp_keys_for_agent
        from app.services.mcp_tool_service import get_allowed_tools

        redis = await get_redis()
        tools: list[BaseTool] = []
        for mcp_key in await get_mcp_keys_for_agent(agent_code):
            # 检查 Redis 中是否存在该 key 的配置
            if not await redis.hexists(_REDIS_KEY, mcp_key):
                continue
            client = await cls.get_client(mcp_key)
            for t in await get_allowed_tools(mcp_key):
                tools.append(_make_lc_tool(
                    client,
                    t["tool_name"],
                    t["tool_desc"] or "",
                    t.get("input_schema") or "",
                ))
        return tools


_JSON_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _json_schema_to_pydantic(schema_str: str, model_name: str) -> Type[BaseModel] | None:
    """
    将 MCP inputSchema（JSON 字符串）转为 Pydantic BaseModel。
    解析失败或 schema 无 properties 时返回 None，调用方降级到 **kwargs。
    """
    if not schema_str:
        return None
    try:
        schema: dict = json.loads(schema_str) if isinstance(schema_str, str) else schema_str
    except Exception:
        return None

    props: dict = schema.get("properties") or {}
    if not props:
        return None

    required: set = set(schema.get("required") or [])
    fields: dict[str, Any] = {}

    for fname, fschema in props.items():
        py_type = _JSON_TYPE_MAP.get(fschema.get("type", "string"), str)
        desc = fschema.get("description", "")
        if fname in required:
            fields[fname] = (py_type, Field(description=desc))
        else:
            fields[fname] = (Optional[py_type], Field(default=None, description=desc))

    try:
        return create_model(model_name, **fields)
    except Exception:
        return None


def _make_lc_tool(client, tool_name: str, tool_desc: str, input_schema: str = "") -> BaseTool:
    """将远端 MCP 工具封装为 LangChain StructuredTool，携带完整参数 schema。"""
    _client = client
    _name = tool_name

    async def _run(**kwargs: Any) -> str:
        return await _client.call_tool(_name, kwargs)

    args_schema = _json_schema_to_pydantic(input_schema, f"{tool_name}_Args")

    return StructuredTool.from_function(
        coroutine=_run,
        name=tool_name,
        description=tool_desc,
        args_schema=args_schema,
    )
