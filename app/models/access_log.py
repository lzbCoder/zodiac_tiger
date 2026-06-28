from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AccessLog(Base):
    """客户端访问记录表。

    经反向代理（nginx/网关）部署时，client_ip 取自 X-Forwarded-For/X-Real-IP，
    回退到直连 socket 地址。详见 app.middleware.access_log。
    """
    __tablename__ = "access_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    referer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    cost_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
