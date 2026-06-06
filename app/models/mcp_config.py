from datetime import datetime
from sqlalchemy import BigInteger, String, Text, SmallInteger, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class McpConfig(Base):
    __tablename__ = "mcp_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(String(255))
    auth_type: Mapped[str] = mapped_column(String(30))
    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timeout: Mapped[int] = mapped_column(Integer, default=20)
    status: Mapped[int] = mapped_column(SmallInteger, default=1)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
