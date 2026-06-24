"""LLM 工厂函数：统一创建 ChatOpenAI 实例，避免重复配置。"""

from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from app.config import settings


def create_llm(model: str, streaming: bool = True, **kwargs) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        api_key=settings.DASHSCOPE_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        streaming=streaming,
        **kwargs,
    )


def resolve_reply_model(config: RunnableConfig | None) -> str:
    """解析"最终 AI 回复"应使用的模型名。

    仅用于最终回复节点：从 config 读取前端选择的 reply_model，命中白名单才采用，
    否则回退默认 CHAT_MODEL。执行过程节点不应调用本函数。
    """
    model = ((config or {}).get("configurable", {}) or {}).get("reply_model")
    if model and model in settings.REPLY_MODELS:
        return model
    return settings.CHAT_MODEL
