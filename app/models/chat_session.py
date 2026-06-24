from datetime import datetime
from sqlalchemy import String, SmallInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatSession(Base):
    """会话主表：会话存在性的唯一来源。

    标题在首条用户消息时写入（title 为空才写），可被重命名覆盖。
    last_time/活跃时间仍由 chat_history 聚合得到，本表不冗余存储。
    """
    __tablename__ = "chat_session"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default="admin")
    title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pinned: Mapped[int] = mapped_column(SmallInteger, default=0)             # 0否 1置顶
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
