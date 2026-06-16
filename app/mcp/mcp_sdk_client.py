"""基于官方 MCP Python SDK 的 Streamable HTTP 客户端封装"""

import json
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class McpStreamHttpClient:
    """
    封装单条 MCP 服务连接，使用 Streamable HTTP 协议。
    每次调用均新建短连接（streamable_http_client 是上下文管理器，不维持持久连接）。
    headers/timeout 通过 httpx.AsyncClient 传入，而非直接传给 streamable_http_client。
    """

    def __init__(self, mcp_key: str, endpoint: str, headers: dict, timeout: int = 30):
        self.mcp_key = mcp_key
        self.endpoint = endpoint
        self.headers = headers
        self.timeout = timeout

    def _make_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self.headers, timeout=self.timeout)

    async def test_and_list_tools(self) -> tuple[bool, str, list[dict]]:
        """
        连通性检测 + 拉取工具列表。
        返回 (ok, message, tools)，tools 格式：[{tool_name, tool_desc, input_schema}, ...]
        """
        try:
            async with self._make_http_client() as http_client:
                # SDK 1.x yield (read, write, get_session_id) 三元组
                async with streamable_http_client(
                    url=self.endpoint,
                    http_client=http_client,
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        resp = await session.list_tools()
                        tools = [
                            {
                                "tool_name": t.name,
                                "tool_desc": t.description or "",
                                "input_schema": (
                                    json.dumps(t.inputSchema, ensure_ascii=False)
                                    if t.inputSchema else ""
                                ),
                            }
                            for t in resp.tools
                        ]
                        return True, "连接成功", tools
        except Exception as e:
            return False, str(e), []

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """
        调用远端 MCP 工具，每次新建短连接。
        返回工具执行结果字符串。
        """
        async with self._make_http_client() as http_client:
            async with streamable_http_client(
                url=self.endpoint,
                http_client=http_client,
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return str(result.content)
