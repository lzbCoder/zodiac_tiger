import asyncio
import json
import time
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from loguru import logger

from app.db.session import get_db_session
from app.db.milvus import get_episodic_collection
from app.models.user_profile import UserProfile
from app.utils.embedding import embed
from app.config import settings

# 各记忆类型的生存时间（毫秒）
_TTL_MS: dict[str, int] = {
    "event": 30 * 24 * 3600 * 1000,
    "goal": 180 * 24 * 3600 * 1000,
    "habit": 180 * 24 * 3600 * 1000,
    "preference": 180 * 24 * 3600 * 1000,
    "relationship": 180 * 24 * 3600 * 1000,
}
_DEFAULT_TTL_MS = 90 * 24 * 3600 * 1000
_DEDUP_SCORE_THRESHOLD = 0.92


async def save_memory(
    user_id: str,
    session_id: str,
    memory_type: str,
    content: str,
    summary: str,
    importance: float,
    metadata: dict | None = None,
) -> None:
    embedding = await embed(summary or content)
    timestamp_ms = int(time.time() * 1000)

    collection = get_episodic_collection()

    # 写入前去重：若已存在余弦相似度极高的记忆则跳过，避免重复积累
    dedup_results = await asyncio.to_thread(
        lambda: collection.search(
            data=[embedding],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=1,
            expr=f'user_id == "{user_id}"',
            output_fields=["importance"],
        )
    )
    for hits in dedup_results:
        for hit in hits:
            if hit.score > _DEDUP_SCORE_THRESHOLD:
                logger.debug(f"跳过重复记忆 (score={hit.score:.3f}): {summary}")
                return

    expires_at = timestamp_ms + _TTL_MS.get(memory_type, _DEFAULT_TTL_MS)
    memory_id = uuid.uuid4().hex

    await asyncio.to_thread(
        lambda: collection.insert([{
            "id": memory_id,
            "user_id": user_id,
            "session_id": session_id,
            "memory_type": memory_type,
            "content": content,
            "summary": summary,
            "embedding": embedding,
            "importance": importance,
            "timestamp": timestamp_ms,
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            "expires_at": expires_at,
        }])
    )
    await asyncio.to_thread(collection.flush)
    logger.debug(f"记忆已保存: [{memory_type}] {summary}")


async def upsert_user_profile(
    user_id: str,
    key: str,
    value: dict,
    source: str | None = None,
    confidence: float = 1.0,
) -> None:
    async with get_db_session() as session:
        stmt = pg_insert(UserProfile).values(
            user_id=user_id,
            key=key,
            value=value,
            source=source,
            confidence=confidence,
        ).on_conflict_do_update(
            constraint="user_profile_user_id_key_key",
            set_={
                "value": value,
                "source": source,
                "confidence": confidence,
            },
        )
        await session.execute(stmt)
        await session.commit()


async def recall_memories(user_id: str, query: str, limit: int = 5) -> list[dict]:
    embedding = await embed(query)
    collection = get_episodic_collection()

    results = await asyncio.to_thread(
        lambda: collection.search(
            data=[embedding],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=limit,
            expr=f'user_id == "{user_id}"',
            output_fields=["memory_type", "content", "summary", "importance", "metadata", "timestamp"],
        )
    )

    memories = []
    for hits in results:
        for hit in hits:
            memories.append({
                "id": hit.id,
                "memory_type": hit.entity.get("memory_type", ""),
                "content": hit.entity.get("content", ""),
                "summary": hit.entity.get("summary", ""),
                "importance": hit.entity.get("importance", 0.5),
                "metadata": hit.entity.get("metadata", "{}"),
                "timestamp": hit.entity.get("timestamp", 0),
                "score": hit.score,  # Milvus COSINE 相似度评分
            })

    # 按 importance × score 综合排序：既保证语义相关（score），又优先展示重要信息（importance）
    memories.sort(key=lambda m: m["importance"] * m["score"], reverse=True)
    return memories


async def get_user_profile(user_id: str) -> list[dict]:
    async with get_db_session() as session:
        stmt = (
            select(UserProfile.key, UserProfile.value, UserProfile.source, UserProfile.confidence)
            .where(UserProfile.user_id == user_id)
            .order_by(UserProfile.key)
        )
        result = await session.execute(stmt)
        rows = result.all()
    return [
        {"key": r.key, "value": r.value, "source": r.source, "confidence": r.confidence}
        for r in rows
    ]


async def extract_and_save(
    user_id: str,
    session_id: str,
    user_message: str,
    ai_response: str,
) -> tuple[int, list[str]]:
    from app.factory.llm_factory import create_llm

    llm = create_llm(settings.MEMORY_SUMMARY_MODEL)

    prompt = f"""请从以下对话中提取值得长期记忆的信息。

包括：
- 用户偏好（喜欢/不喜欢什么）
- 长期目标（计划做什么）
- 稳定习惯（经常做什么）
- 重要事件（发生了什么）
- 人际关系（认识谁）

不要提取：
- 临时聊天内容
- 一次性问题
- 无长期价值的信息

用户消息: {user_message}
AI回复: {ai_response}

返回纯 JSON 数组格式，不要包含 markdown 代码块标记：
[{{"type": "preference|goal|habit|event|relationship", "content": "记忆内容", "summary": "简短摘要", "importance": 0.0-1.0}}]

如果没有值得保存的内容，返回 []。

示例：
用户消息: 我叫李志斌，今年28岁，是一名软件工程师
AI回复: 你好李志斌！很高兴认识你
返回: [{{"type": "preference", "content": "用户职业是软件工程师", "summary": "软件工程师", "importance": 0.8}}, {{"type": "preference", "content": "用户年龄28岁", "summary": "28岁", "importance": 0.6}}, {{"type": "relationship", "content": "用户自我介绍叫李志斌", "summary": "用户姓名李志斌", "importance": 0.7}}]

用户消息: 我喜欢吃香蕉，还喜欢打篮球
AI回复: 香蕉很健康，打篮球也是好运动！
返回: [{{"type": "preference", "content": "用户喜欢吃香蕉", "summary": "喜欢吃香蕉", "importance": 0.7}}, {{"type": "habit", "content": "用户喜欢打篮球", "summary": "喜欢打篮球", "importance": 0.6}}]

用户消息: 今天去超市买了些东西
AI回复: 好的，购物愉快！
返回: []
"""

    resp = await llm.ainvoke(prompt)

    try:
        text = resp.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
            if text.endswith("```"):
                text = text[:-3]
        items = json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"记忆提取 LLM 返回非 JSON: {resp.content[:200]}")
        return 0, []

    if not items:
        return 0, []

    count = 0
    summaries: list[str] = []
    for item in items:
        try:
            mem_type = item["type"]
            content = item["content"]
            summary = item.get("summary", content[:100])
            importance = float(item.get("importance", 0.5))

            await save_memory(
                user_id=user_id,
                session_id=session_id,
                memory_type=mem_type,
                content=content,
                summary=summary,
                importance=importance,
            )

            # 偏好类 + 高置信度 → 同步写入 user_profile
            if mem_type == "preference" and importance >= 0.7:
                await upsert_user_profile(
                    user_id=user_id,
                    key=f"pref:{summary[:60]}",
                    value={"content": content, "importance": importance},
                    source=session_id,
                    confidence=importance,
                )

            summaries.append(f"[{mem_type}] {summary}")
            count += 1
        except Exception as e:
            logger.warning(f"保存记忆失败: {e}")

    if count > 0:
        logger.info(f"记忆提取完成: {count} 条 (session={session_id})")
    return count, summaries
