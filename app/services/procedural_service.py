"""程序记忆服务：保存（去重/合并/版本化）、召回（语义 TopK + 置信加权）。

真值源 Postgres（procedural_memories），语义索引 Milvus（procedural_memory 集合）。
与对话记忆（episodic）完全隔离。
"""
import asyncio
import time
import uuid
from datetime import datetime

from sqlalchemy import select, update
from loguru import logger

from app.db.session import get_db_session
from app.db.milvus import get_procedural_collection
from app.models.procedural_memory import ProceduralMemory
from app.utils.embedding import embed
from app.config import settings


def _score(success: int, failure: int) -> float:
    """Laplace 平滑置信度。"""
    return (success + 1) / (success + failure + 2)


async def _milvus_search(embedding: list[float], user_id: str, limit: int) -> list[tuple[str, float]]:
    """返回 [(memory_id, similarity), ...]。"""
    collection = get_procedural_collection()
    results = await asyncio.to_thread(
        lambda: collection.search(
            data=[embedding],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=limit,
            expr=f'user_id == "{user_id}"',
            output_fields=["memory_id"],
        )
    )
    out: list[tuple[str, float]] = []
    for hits in results:
        for hit in hits:
            out.append((hit.entity.get("memory_id", hit.id), hit.score))
    return out


async def save_rule(
    user_id: str,
    content: str,
    memory_type: str = "rule",
    source_task_type: str | None = None,
    success: bool = True,
) -> None:
    """保存一条规则：相似度≥阈值则合并到已有规则（计数/置信更新），否则新建。"""
    embedding = await embed(content)

    # 去重/合并：查最相似的一条
    try:
        hits = await _milvus_search(embedding, user_id, limit=1)
    except Exception as e:
        logger.warning(f"[程序记忆] Milvus 查重失败，按新建处理: {e}")
        hits = []

    matched_id = None
    if hits and hits[0][1] >= settings.PROCEDURAL_DEDUP_THRESHOLD:
        matched_id = hits[0][0]

    async with get_db_session() as session:
        if matched_id:
            existing = (await session.execute(
                select(ProceduralMemory).where(ProceduralMemory.memory_id == matched_id)
            )).scalar_one_or_none()
            if existing:
                s = existing.success_count + (1 if success else 0)
                f = existing.failure_count + (0 if success else 1)
                await session.execute(
                    update(ProceduralMemory).where(ProceduralMemory.memory_id == matched_id).values(
                        success_count=s, failure_count=f, score=_score(s, f),
                        status=1, updated_at=datetime.now(),
                    )
                )
                await session.commit()
                logger.info(f"[程序记忆] 合并已有规则 {matched_id[:8]} (score={_score(s, f):.2f})")
                return

        # 新建
        memory_id = uuid.uuid4().hex
        s, f = (1, 0) if success else (0, 1)
        session.add(ProceduralMemory(
            memory_id=memory_id, user_id=user_id, memory_type=memory_type,
            title=content[:60], content=content, source_task_type=source_task_type,
            success_count=s, failure_count=f, score=_score(s, f), status=1,
        ))
        await session.commit()

    # 双写 Milvus
    try:
        collection = get_procedural_collection()
        await asyncio.to_thread(lambda: collection.insert([{
            "memory_id": memory_id,
            "user_id": user_id,
            "source_task_type": source_task_type or "",
            "embedding": embedding,
            "timestamp": int(time.time() * 1000),
        }]))
        await asyncio.to_thread(collection.flush)
        logger.info(f"[程序记忆] 新建规则 {memory_id[:8]} [{memory_type}]")
    except Exception as e:
        logger.warning(f"[程序记忆] Milvus 写入失败（PG 已保存）: {e}")


async def recall_rules(user_id: str, task: str, limit: int | None = None) -> list[dict]:
    """语义召回有效规则，按 score×相似度排序；命中即 bump hit_count/last_hit_at。"""
    limit = limit or settings.PROCEDURAL_RECALL_LIMIT
    try:
        embedding = await embed(task)
        hits = await _milvus_search(embedding, user_id, limit=limit * 2)
    except Exception as e:
        logger.warning(f"[程序记忆] 召回失败: {e}")
        return []
    if not hits:
        return []

    sim_map = {mid: sim for mid, sim in hits}
    async with get_db_session() as session:
        rows = (await session.execute(
            select(ProceduralMemory).where(
                ProceduralMemory.memory_id.in_(list(sim_map.keys())),
                ProceduralMemory.status == 1,
            )
        )).scalars().all()

        ranked = sorted(
            ({"memory_id": r.memory_id, "content": r.content, "memory_type": r.memory_type,
              "title": r.title, "score": r.score, "sim": sim_map.get(r.memory_id, 0.0)}
             for r in rows),
            key=lambda d: d["score"] * d["sim"], reverse=True,
        )[:limit]

        if ranked:
            ids = [d["memory_id"] for d in ranked]
            await session.execute(
                update(ProceduralMemory).where(ProceduralMemory.memory_id.in_(ids)).values(
                    hit_count=ProceduralMemory.hit_count + 1, last_hit_at=datetime.now(),
                )
            )
            await session.commit()
    return ranked
