"""工具包：共享工具级重试装饰器（@tool 工具与 MCP _run 协程共用）。"""

from tenacity import retry, stop_after_attempt, wait_exponential

# 工具/外部调用统一重试：最多 3 次，指数退避；耗尽后抛原异常（由调用方捕获）
tool_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=8),
    reraise=True,
)
