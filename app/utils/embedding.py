from dashscope import TextEmbedding
from app.config import settings


async def embed(text: str) -> list[float]:
    """单文本向量化，返回 1024 维 float 向量。"""
    resp = TextEmbedding.call(
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
