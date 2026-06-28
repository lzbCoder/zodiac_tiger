"""访问记录中间件：提取真实客户端 IP 并落访问日志。

部署在 nginx/网关反向代理之后时，request.client.host 拿到的是代理/容器内网 IP，
真实客户端 IP 需从代理透传的请求头获取。本中间件按以下优先级解析：
    1. X-Forwarded-For  —— 取最左 IP（最初的客户端；代理需正确透传）
    2. X-Real-IP
    3. request.client.host —— 直连兜底（无代理时即真实 IP）

代理透传配置（nginx proxy_set_header）由运维侧保证；本层只负责按约定解析。
"""
import time

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.services.access_log_service import record_access


def get_client_ip(request: Request) -> str:
    """按 XFF → X-Real-IP → 直连 socket 的优先级解析真实客户端 IP。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # "client, proxy1, proxy2" → 取最左侧的原始客户端
        first = xff.split(",")[0].strip()
        if first:
            return first
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # CORS 预检不算业务访问，跳过
        if request.method == "OPTIONS":
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        cost_ms = int((time.perf_counter() - start) * 1000)

        # 记录失败绝不能影响请求返回
        try:
            session_id = request.query_params.get("session_id")
            await record_access(
                client_ip=get_client_ip(request),
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                cost_ms=cost_ms,
                user_agent=request.headers.get("user-agent", ""),
                referer=request.headers.get("referer", ""),
                session_id=session_id,
            )
        except Exception as e:
            logger.warning(f"访问记录中间件异常: {e}")

        return response
