import asyncio

from dashscope import TextEmbedding
from app.config import settings


async def embed(text: str) -> list[float]:
    """单文本向量化，返回 1024 维 float 向量。

    dashscope SDK 为同步阻塞调用，放入线程池执行，避免阻塞事件循环
    （否则记忆召回等节点会卡住整个 SSE 流式响应）。
    """
    resp = await asyncio.to_thread(
        TextEmbedding.call,
        model=settings.EMBEDDING_MODEL,
        input=text,
        api_key=settings.DASHSCOPE_API_KEY,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Embedding 调用失败: {resp.message}")
    return resp.output["embeddings"][0]["embedding"]


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """批量文本向量化。"""
    embeddings = []
    for text in texts:
        emb = await embed(text)
        embeddings.append(emb)
    return embeddings
