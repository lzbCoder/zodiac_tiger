import httpx
from loguru import logger


class McpClient:
    """通用 MCP HTTP 客户端，封装外部服务调用。"""

    @staticmethod
    async def call(
        url: str,
        auth_type: str = "none",
        api_key: str | None = None,
        timeout: int = 20,
        params: dict | None = None,
        body: dict | None = None,
    ) -> dict:
        """调用外部 MCP 服务，返回 JSON 结果。"""
        headers = {"Content-Type": "application/json"}
        if auth_type == "api_key" and api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif auth_type == "token" and api_key:
            headers["Authorization"] = api_key

        async with httpx.AsyncClient(timeout=timeout) as client:
            if body:
                resp = await client.post(url, json=body, headers=headers, params=params)
            else:
                resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    async def test(
        url: str,
        auth_type: str = "none",
        api_key: str | None = None,
        timeout: int = 10,
    ) -> bool:
        """测试 MCP 服务连通性。"""
        try:
            headers = {}
            if auth_type == "api_key" and api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            elif auth_type == "token" and api_key:
                headers["Authorization"] = api_key

            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=headers)
                return resp.status_code < 500
        except Exception as e:
            logger.warning(f"MCP 连接测试失败 [{url}]: {e}")
            return False
