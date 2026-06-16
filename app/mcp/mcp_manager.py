"""全局 MCP 管理器：配置缓存 + LangChain 工具转换"""

import json
from typing import Any, Optional, Type

from loguru import logger
from pydantic import BaseModel, Field, create_model
from langchain_core.tools import BaseTool, StructuredTool


class GlobalMcpManager:
    """
    管理所有远程 MCP 服务的配置缓存。
    不维持持久连接——每次工具调用均通过 McpStreamHttpClient 新建短连接。
    """

    _config_cache: dict[str, dict] = {}  # mcp_key → {endpoint_url, auth_headers}

    @classmethod
    async def init(cls):
        """启动时从数据库加载所有 enable_status=1 的服务配置到内存缓存。"""
        from sqlalchemy import select
        from app.db.session import get_db_session
        from app.models.mcp_server import McpServerConfig

        async with get_db_session() as session:
            stmt = select(McpServerConfig).where(McpServerConfig.enable_status == 1)
            rows = (await session.execute(stmt)).scalars().all()

        cls._config_cache = {
            r.mcp_key: {"endpoint_url": r.endpoint_url, "auth_headers": r.auth_headers or {}}
            for r in rows
        }
        logger.info(f"MCP Manager 初始化完成，加载 {len(cls._config_cache)} 个服务")

    @classmethod
    def reload(cls, mcp_key: str, endpoint_url: str, auth_headers: dict):
        """新增/编辑/启用后刷新单条缓存（同步调用，无需 await）。"""
        cls._config_cache[mcp_key] = {"endpoint_url": endpoint_url, "auth_headers": auth_headers}

    @classmethod
    def remove(cls, mcp_key: str):
        """删除/禁用后移除缓存。"""
        cls._config_cache.pop(mcp_key, None)

    @classmethod
    def get_client(cls, mcp_key: str):
        """按 mcp_key 返回新 McpStreamHttpClient 实例（使用缓存配置，不建连）。"""
        from app.mcp.mcp_sdk_client import McpStreamHttpClient
        cfg = cls._config_cache[mcp_key]
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
        from app.services.agent_mcp_service import get_mcp_keys_for_agent
        from app.services.mcp_tool_service import get_allowed_tools

        tools: list[BaseTool] = []
        for mcp_key in await get_mcp_keys_for_agent(agent_code):
            if mcp_key not in cls._config_cache:
                continue
            client = cls.get_client(mcp_key)
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
        args_schema=args_schema,  # None 时 StructuredTool 自动降级到 **kwargs
    )
