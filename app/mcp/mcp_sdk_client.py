"""基于官方 MCP Python SDK 的多协议客户端封装（Streamable HTTP / SSE）"""

import asyncio
import json
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.client.sse import sse_client


class McpStreamHttpClient:
    """
    Streamable HTTP 协议客户端。
    协议本身无状态，每次调用独立建 HTTP 连接，无冷启动问题。
    """

    def __init__(self, mcp_key: str, endpoint: str, headers: dict, timeout: int = 30):
        self.mcp_key = mcp_key
        self.endpoint = endpoint
        self.headers = headers
        self.timeout = timeout

    def _make_http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(headers=self.headers, timeout=self.timeout)

    async def test_and_list_tools(self) -> tuple[bool, str, list[dict]]:
        try:
            async with self._make_http_client() as http_client:
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
        async with self._make_http_client() as http_client:
            async with streamable_http_client(
                url=self.endpoint,
                http_client=http_client,
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)
                    return str(result.content)

    def close(self):
        pass  # 无持久状态


# ── SSE 持久连接缓存 ──────────────────────────────────────────────────────────
# 同一 mcp_key 对应同一 McpSseClient 实例，复用后台 SSE 连接，避免每次冷启动。
# 测试用临时 key "_test" 不入缓存，由调用方负责 close()。
_sse_client_cache: dict[str, "McpSseClient"] = {}


class McpSseClient:
    """
    SSE 协议客户端，维持持久化后台 SSE 连接。

    问题背景：
    SSE 是有状态协议——服务端需为每条长连接分配资源（百炼等 serverless 实现会
    启动一个按需容器），因此首次建连需要冷启动（实测 2-5 分钟）。
    如果每次 call_tool 都新建 SSE 连接，则每次工具调用都要冷启动，完全不可用。

    解决方案：
    用后台 asyncio Task 持续持有 sse_client 上下文，将 ClientSession 缓存复用。
    冷启动只发生一次（或连接断开后重连时）；call_tool 等到连接就绪后立即执行。
    """

    def __init__(self, mcp_key: str, endpoint: str, headers: dict, timeout: int = 30):
        self.mcp_key = mcp_key
        self.endpoint = endpoint
        self.headers = headers
        self.timeout = timeout

        self._session: ClientSession | None = None
        self._session_error: Exception | None = None
        self._bg_task: asyncio.Task | None = None
        # 延迟初始化：asyncio 对象必须在事件循环内创建
        self._ready: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None

    def _init_primitives(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        if self._ready is None:
            self._ready = asyncio.Event()

    async def _run_connection(self):
        """后台任务：建立并持续维持 SSE 长连接，直到被取消或连接意外断开。"""
        try:
            async with sse_client(
                url=self.endpoint,
                headers=self.headers,
                timeout=self.timeout,
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    self._ready.set()           # 通知所有等待方：连接就绪
                    await asyncio.sleep(86400)  # 持续保持上下文存活（最长 24h）
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._session_error = e
            if self._ready:
                self._ready.set()   # 让等待方感知错误
        finally:
            self._session = None

    async def _get_session(self, connect_timeout: float = 360.0) -> ClientSession:
        """
        获取可用的 ClientSession。
        如果后台连接尚未建立或已断开，则启动新的后台任务并等待就绪。
        多个并发调用共享同一后台任务，不会重复建连。
        """
        self._init_primitives()
        async with self._lock:
            if self._bg_task is None or self._bg_task.done():
                self._ready.clear()
                self._session_error = None
                self._session = None
                self._bg_task = asyncio.create_task(self._run_connection())

        if not self._ready.is_set():
            try:
                await asyncio.wait_for(self._ready.wait(), timeout=connect_timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"SSE 连接超时（{connect_timeout:.0f}s 内未建立连接，"
                    "百炼等 serverless 服务冷启动可能需要 3-5 分钟）"
                )

        if self._session_error:
            err = self._session_error
            self._session_error = None
            raise err
        if self._session is None:
            raise RuntimeError("SSE 会话建立失败")
        return self._session

    def close(self):
        """主动关闭后台连接（配置变更、删除服务或测试结束时调用）。"""
        if self._bg_task and not self._bg_task.done():
            self._bg_task.cancel()
        self._session = None

    async def test_and_list_tools(self) -> tuple[bool, str, list[dict]]:
        """
        连通性检测 + 拉取工具列表。
        首次调用时触发后台建连（冷启动），后续调用复用已有连接。
        """
        try:
            session = await self._get_session(connect_timeout=360.0)
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
        except TimeoutError as e:
            return False, str(e), []
        except Exception as e:
            return False, str(e), []

    async def call_tool(self, tool_name: str, arguments: dict) -> str:
        """
        调用工具，复用已建立的持久 SSE 连接，无需冷启动。
        若连接已断开，自动重连（会有一次冷启动延迟）。
        """
        session = await self._get_session(connect_timeout=360.0)
        result = await session.call_tool(tool_name, arguments)
        return str(result.content)


def invalidate_sse_client(mcp_key: str):
    """MCP 配置变更或删除时调用：关闭旧连接并从缓存移除，下次使用时将以新配置重建。"""
    client = _sse_client_cache.pop(mcp_key, None)
    if client:
        client.close()


def create_mcp_client(
    mcp_key: str,
    endpoint: str,
    headers: dict,
    transport_type: str = "streamable_http",
    timeout: int = 30,
) -> McpStreamHttpClient | McpSseClient:
    """
    根据 transport_type 创建并返回 MCP 客户端实例。
    SSE：使用模块级缓存，同一 mcp_key 返回同一实例以复用持久连接。
    Streamable HTTP：每次返回新实例（无状态，无需复用）。
    """
    if transport_type == "sse":
        if mcp_key != "_test" and mcp_key in _sse_client_cache:
            return _sse_client_cache[mcp_key]
        client = McpSseClient(
            mcp_key=mcp_key, endpoint=endpoint, headers=headers, timeout=timeout
        )
        if mcp_key != "_test":
            _sse_client_cache[mcp_key] = client
        return client
    return McpStreamHttpClient(
        mcp_key=mcp_key, endpoint=endpoint, headers=headers, timeout=timeout
    )
