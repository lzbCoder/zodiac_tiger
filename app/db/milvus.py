from pymilvus import (
    connections,
    db,
    Collection,
    FieldSchema,
    CollectionSchema,
    DataType,
    utility,
)
from loguru import logger

from app.config import settings


def connect_milvus() -> None:
    connections.connect(
        alias="default",
        uri=settings.MILVUS_URI,
        token=settings.MILVUS_TOKEN,
    )

    databases = db.list_database()
    if settings.MILVUS_DATABASE not in databases:
        db.create_database(settings.MILVUS_DATABASE)
        logger.info(f"Milvus 数据库 {settings.MILVUS_DATABASE} 创建成功")

    db.using_database(settings.MILVUS_DATABASE)
    logger.info(f"Milvus 连接成功，使用数据库: {settings.MILVUS_DATABASE}")


def _create_assistant_collection() -> None:
    if utility.has_collection(settings.MILVUS_COLLECTION_ASSISTANT):
        logger.info(f"集合 {settings.MILVUS_COLLECTION_ASSISTANT} 已存在，跳过创建")
        return

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
        FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=settings.MILVUS_VECTOR_DIM),
        FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=4096),
        FieldSchema(name="meta_type", dtype=DataType.VARCHAR, max_length=20),
        FieldSchema(name="meta_id", dtype=DataType.VARCHAR, max_length=64),
    ]
    schema = CollectionSchema(fields, description="智能助手向量集合")
    Collection(name=settings.MILVUS_COLLECTION_ASSISTANT, schema=schema)
    logger.info(f"集合 {settings.MILVUS_COLLECTION_ASSISTANT} 创建成功 (维度={settings.MILVUS_VECTOR_DIM})")


def _create_episodic_collection() -> None:
    if utility.has_collection(settings.MILVUS_COLLECTION_EPISODIC):
        logger.info(f"集合 {settings.MILVUS_COLLECTION_EPISODIC} 已存在，跳过创建")
        return

    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
        FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="session_id", dtype=DataType.VARCHAR, max_length=64),
        FieldSchema(name="memory_type", dtype=DataType.VARCHAR, max_length=30),
        FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=4096),
        FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=settings.MILVUS_VECTOR_DIM),
        FieldSchema(name="importance", dtype=DataType.FLOAT),
        FieldSchema(name="timestamp", dtype=DataType.INT64),
        FieldSchema(name="metadata", dtype=DataType.JSON),
        FieldSchema(name="expires_at", dtype=DataType.INT64),
    ]
    schema = CollectionSchema(fields, description="长期记忆集合")
    collection = Collection(name=settings.MILVUS_COLLECTION_EPISODIC, schema=schema)

    index_params = {
        "metric_type": "COSINE",
        "index_type": "IVF_FLAT",
        "params": {"nlist": 128},
    }
    collection.create_index(field_name="embedding", index_params=index_params)
    collection.load()

    logger.info(f"集合 {settings.MILVUS_COLLECTION_EPISODIC} 创建成功 (维度={settings.MILVUS_VECTOR_DIM}, 索引已创建)")


def init_collection() -> None:
    """初始化所有 Milvus 集合（幂等）。"""
    _create_assistant_collection()
    _create_episodic_collection()


def get_assistant_collection() -> Collection:
    return Collection(name=settings.MILVUS_COLLECTION_ASSISTANT)


def get_episodic_collection() -> Collection:
    return Collection(name=settings.MILVUS_COLLECTION_EPISODIC)
