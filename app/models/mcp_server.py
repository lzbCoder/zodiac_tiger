from datetime import datetime
from sqlalchemy import BigInteger, String, SmallInteger, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class McpServerConfig(Base):
    __tablename__ = "mcp_server_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mcp_key: Mapped[str] = mapped_column(String(128), unique=True)
    display_name: Mapped[str] = mapped_column(String(256))
    endpoint_url: Mapped[str] = mapped_column(String(512))
    auth_headers: Mapped[dict] = mapped_column(JSONB, default=dict)
    enable_status: Mapped[int] = mapped_column(SmallInteger, default=1)
    connect_status: Mapped[int] = mapped_column(SmallInteger, default=0)
    last_check_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(512), nullable=True)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    update_time: Mapped[datetime | None] = mapped_column(DateTime, onupdate=func.now(), nullable=True)
