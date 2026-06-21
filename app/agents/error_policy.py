"""统一节点异常处理策略：节点重试/超时/错误处理。

- set_node_defaults 用 DEFAULT_RETRY / DEFAULT_TIMEOUT / log_and_raise；
- 需禁用重试的节点用 NO_RETRY（不能传 None——None 会继承默认）。

工具级重试装饰器见 app.tools.tool_retry。
"""

from langgraph.types import RetryPolicy
from langgraph.errors import NodeError
from langgraph.config import get_config
from loguru import logger

from app.services import execution_error_service

# ---- 节点级策略 ----

# LangGraph 默认判定（重试 ConnectionError/5xx/超时，不重试 ValueError/TypeError/OSError 等）。
# 取 RetryPolicy 默认值，避免 import 私有的 langgraph._internal._retry。
_default_retry_on = RetryPolicy().retry_on


def _node_retry_on(exc: BaseException) -> bool:
    """节点重试判定：在 LangGraph 默认基础上，额外把 AttributeError 视为不可恢复（不重试）。"""
    if isinstance(exc, AttributeError):
        return False
    return _default_retry_on(exc)


DEFAULT_RETRY = RetryPolicy(max_attempts=3, retry_on=_node_retry_on)  # 可恢复重试；AttributeError 等不可恢复不重试
NO_RETRY = RetryPolicy(max_attempts=1)        # 显式禁用重试（None 会继承默认，必须用这个）

DEFAULT_TIMEOUT = 60                           # 默认节点超时(秒)
LONG_TIMEOUT = 120                             # 长输出/慢节点超时(秒)
SUBGRAPH_TIMEOUT = 600                         # 整个子图作为主图节点的宽松上限(秒)

# 子图 wrapper 节点：内部 handler 已记录真实失败节点，避免在主图层重复入库
_SUBGRAPH_WRAPPERS = {"report_agent", "travel_agent", "assistant_agent"}


async def log_and_raise(state, error: NodeError):
    """全局节点错误处理：打印日志 + 写 execution_error_log，再重新抛出使本轮任务优雅中止。"""
    cfg = get_config().get("configurable", {}) if get_config() else {}
    if error.node not in _SUBGRAPH_WRAPPERS:
        try:
            await execution_error_service.save_error(
                cfg.get("session_id", ""), cfg.get("chat_id", ""),
                error.node, error.error,
            )
        except Exception as e:
            logger.error(f"写 execution_error_log 失败: {e}")
    logger.opt(exception=error.error).error(f"[node:{error.node}] 执行失败: {error.error}")
    raise error.error
