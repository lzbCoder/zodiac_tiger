"""工具包：共享工具级重试装饰器（@tool 工具与 MCP _run 协程共用）。

策略与节点层 error_policy.default_retry_on 对齐——只重试“瞬时/可恢复”异常
（网络连接、超时、HTTP 5xx），不重试参数错误、JSON 解析等不可恢复异常，避免空跑。
"""

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

try:
    import requests  # langchain 传递依赖，Tavily 等基于 requests
    _requests_exc = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
except Exception:  # pragma: no cover
    requests = None
    _requests_exc = ()


def _is_transient(e: BaseException) -> bool:
    """判定是否为可重试的瞬时异常（与节点层 default_retry_on 思路一致）。"""
    # 连接 / 超时 / 读写等传输层错误（httpx.TimeoutException 也是 TransportError 子类）
    if isinstance(e, (ConnectionError, TimeoutError, httpx.TransportError)):
        return True
    # HTTP 5xx：服务端临时故障可重试；4xx 等客户端错误不重试
    if isinstance(e, httpx.HTTPStatusError):
        return 500 <= e.response.status_code < 600
    if requests is not None and isinstance(e, requests.exceptions.HTTPError):
        resp = getattr(e, "response", None)
        return resp is not None and 500 <= resp.status_code < 600
    if isinstance(e, _requests_exc):
        return True
    return False


def _log_retry(retry_state) -> None:
    """每次失败将重试前打一条 WARNING，便于观测。"""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    fn = getattr(retry_state.fn, "__name__", "tool")
    logger.warning(f"[tool_retry] {fn} 第 {retry_state.attempt_number} 次调用失败，准备重试: {exc!r}")


# 工具/外部调用统一重试：仅瞬时异常重试，最多 3 次，指数退避；耗尽后抛原异常（由调用方捕获）
tool_retry = retry(
    stop=stop_after_attempt(3),                   # 最多尝试 3 次（1 次初始 + 2 次重试）
    wait=wait_exponential(multiplier=0.5, max=8), # 重试间隔：0.5 * 2^(n-1)，上限 8s
    retry=retry_if_exception(_is_transient),      # 仅瞬时异常才重试
    before_sleep=_log_retry,                      # 每次重试前打日志
    reraise=True,                                 # 耗尽后抛原始异常
)
