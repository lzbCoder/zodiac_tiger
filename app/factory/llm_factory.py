"""LLM 工厂函数：统一创建 ChatOpenAI 实例，避免重复配置。"""

from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from app.config import settings


class _DashScopeChatOpenAI(ChatOpenAI):
    """补回第三方 reasoning_content 的 ChatOpenAI 子类。

    官方 ChatOpenAI 只解析标准 OpenAI 字段，明确丢弃 dashscope/deepseek 等
    在 delta 里返回的 `reasoning_content`（见 langchain_openai 源码文件头注释）。
    这里在流式 chunk 转换后，把 reasoning_content 补进 additional_kwargs，
    供 SSE 层（_chunk_reasoning）把思考过程实时上屏。
    """

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        gen = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        if gen is None:
            return None
        try:
            choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
            delta = (choices[0].get("delta") or {}) if choices else {}
            reasoning = delta.get("reasoning_content")
            if reasoning:
                gen.message.additional_kwargs["reasoning_content"] = reasoning
        except Exception:
            pass
        return gen


def create_llm(model: str, streaming: bool = True, **kwargs) -> ChatOpenAI:
    return _DashScopeChatOpenAI(
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


def thinking_extra_body(model: str, enable: bool) -> dict:
    """按模型返回"开/关思考"对应的 extra_body 参数（不同厂商参数名不同）。

    - qwen3 系列：enable_thinking 布尔开关，可双向控制；
    - deepseek 推理型：思考恒开、无法关闭，返回 {}（不下发，避免报错）；
    - 其余/未知模型：返回 {}，不下发该参数。
    """
    m = (model or "").lower()
    if m.startswith("qwen3"):
        return {"enable_thinking": enable}
    return {}


def create_reply_llm(config: RunnableConfig | None, streaming: bool = True) -> ChatOpenAI:
    """构造"最终 AI 回复"专用 LLM：解析前端模型 + 按 show_reasoning 开/关思考。

    show_reasoning 来自 config.configurable.enable_thinking（前端"显示思维链"开关）。
    """
    model = resolve_reply_model(config)
    enable = bool(((config or {}).get("configurable", {}) or {}).get("enable_thinking", False))
    extra = thinking_extra_body(model, enable)
    kwargs = {"extra_body": extra} if extra else {}
    return create_llm(model, streaming=streaming, **kwargs)
