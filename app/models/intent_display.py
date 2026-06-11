from datetime import datetime
from sqlalchemy import BigInteger, String, Text, Integer, SmallInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IntentDisplayConfig(Base):
    __tablename__ = "intent_display_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    intent_key: Mapped[str] = mapped_column(String(64), unique=True, comment="后端意图key: travel/report/chat")
    show_name: Mapped[str] = mapped_column(String(64), comment="用户端展示名称")
    intent_desc: Mapped[str] = mapped_column(Text, comment="能力详细介绍")
    demo_input: Mapped[str] = mapped_column(Text, comment="用户示例提问")
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="前端图标标识")
    sort: Mapped[int] = mapped_column(Integer, default=0, comment="前端展示排序")
    enable: Mapped[int] = mapped_column(SmallInteger, default=1, comment="是否对用户可见 0隐藏 1展示")
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    update_time: Mapped[datetime | None] = mapped_column(DateTime, onupdate=func.now(), nullable=True)
