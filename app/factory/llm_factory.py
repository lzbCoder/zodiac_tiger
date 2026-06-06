"""LLM 工厂函数：统一创建 ChatOpenAI 实例，避免重复配置。"""

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
