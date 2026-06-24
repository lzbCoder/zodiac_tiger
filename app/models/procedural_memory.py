from datetime import datetime
from sqlalchemy import BigInteger, Integer, SmallInteger, String, Text, Float, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProceduralMemory(Base):
    """程序记忆：从任务执行过程复盘出的"可复用规则"，带版本与置信度。

    真值源在 Postgres（计数/置信/状态），语义召回向量在 Milvus procedural_memory 集合。
    """
    __tablename__ = "procedural_memories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    memory_id: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(String(64), default="admin")
    memory_type: Mapped[str | None] = mapped_column(String(50), nullable=True)   # rule/pitfall/preference
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text)                                    # 规则正文
    source_task_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[int] = mapped_column(SmallInteger, default=1)                  # 1 有效 / 0 失效
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
