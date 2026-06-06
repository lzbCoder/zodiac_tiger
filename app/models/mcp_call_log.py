from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class McpCallLog(Base):
    __tablename__ = "mcp_call_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mcp_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
