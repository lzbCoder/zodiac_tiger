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
                    {
                        "endpoint_url": r.endpoint_url,
                        "auth_headers": r.auth_headers or {},
                        "transport_type": r.transport_type or "streamable_http",
                    },
                    ensure_ascii=False,
                )
                for r in rows
            }
            await redis.hset(_REDIS_KEY, mapping=mapping)

        logger.info(f"MCP Manager 初始化完成，加载 {len(rows)} 个服务")

    @classmethod
    async def warmup_sse(cls):
        """
        后台预热所有 SSE 类型的 MCP 服务连接。
        在 lifespan 启动后以 asyncio.create_task 调用，不阻塞启动流程。
        SSE 冷启动（百炼等 serverless 实现需要 3-5 分钟）在后台静默完成，
        之后工具调用直接复用已建立的连接，无需等待。
        """
        from app.db.redis import get_redis
        from app.mcp.mcp_sdk_client import create_mcp_client

        try:
            redis = await get_redis()
            all_keys = await redis.hkeys(_REDIS_KEY)
            if not all_keys:
                return

            import json as _json
            sse_keys = []
            for key in all_keys:
                raw = await redis.hget(_REDIS_KEY, key)
                if raw:
                    cfg = _json.loads(raw)
                    if cfg.get("transport_type") == "sse":
                        sse_keys.append((key, cfg))

            if not sse_keys:
                return

            from loguru import logger as _logger
            _logger.info(f"SSE 预热：开始后台建连 {[k for k, _ in sse_keys]}")

            async def _warmup_one(mcp_key: str, cfg: dict):
                try:
                    client = create_mcp_client(
                        mcp_key=mcp_key,
                        endpoint=cfg["endpoint_url"],
                        headers=cfg.get("auth_headers", {}),
                        transport_type="sse",
                    )
                    # 触发建连并等待就绪（冷启动在此发生）
                    ok, msg, tools = await client.test_and_list_tools()
                    if ok:
                        _logger.info(f"SSE 预热完成 [{mcp_key}]：{len(tools)} 个工具，连接已就绪")
                    else:
                        _logger.warning(f"SSE 预热失败 [{mcp_key}]：{msg}")
                except Exception as e:
                    _logger.error(f"SSE 预热异常 [{mcp_key}]：{e}")

            import asyncio as _asyncio
            await _asyncio.gather(*[_warmup_one(k, cfg) for k, cfg in sse_keys])
        except Exception as e:
            from loguru import logger as _logger
            _logger.error(f"SSE 预热任务异常: {e}")

    @classmethod
    async def reload(cls, mcp_key: str, endpoint_url: str, auth_headers: dict, transport_type: str = "streamable_http"):
        """新增/编辑/启用后刷新单条 Redis 缓存，同时关闭旧 SSE 持久连接（配置已变，旧连接失效）。"""
        from app.db.redis import get_redis
        from app.mcp.mcp_sdk_client import invalidate_sse_client

        invalidate_sse_client(mcp_key)  # 关闭旧 SSE 连接，下次调用以新配置重建
        redis = await get_redis()
        await redis.hset(
            _REDIS_KEY,
            mcp_key,
            json.dumps(
                {"endpoint_url": endpoint_url, "auth_headers": auth_headers, "transport_type": transport_type},
                ensure_ascii=False,
            ),
        )

    @classmethod
    async def remove(cls, mcp_key: str):
        """删除/禁用后移除 Redis 缓存条目，同时关闭 SSE 持久连接。"""
        from app.db.redis import get_redis
        from app.mcp.mcp_sdk_client import invalidate_sse_client

        invalidate_sse_client(mcp_key)
        redis = await get_redis()
        await redis.hdel(_REDIS_KEY, mcp_key)

    @classmethod
    async def get_client(cls, mcp_key: str):
        """按 mcp_key 从 Redis 读取配置，根据 transport_type 返回对应协议客户端实例。"""
        from app.mcp.mcp_sdk_client import create_mcp_client
        from app.db.redis import get_redis

        redis = await get_redis()
        raw = await redis.hget(_REDIS_KEY, mcp_key)
        if not raw:
            raise KeyError(f"MCP 配置不存在: {mcp_key}")
        cfg = json.loads(raw)
        return create_mcp_client(
            mcp_key=mcp_key,
            endpoint=cfg["endpoint_url"],
            headers=cfg["auth_headers"],
            transport_type=cfg.get("transport_type", "streamable_http"),
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
