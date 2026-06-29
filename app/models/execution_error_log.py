from datetime import datetime
from sqlalchemy import BigInteger, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExecutionErrorLog(Base):
    __tablename__ = "execution_error_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_node_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_node_display_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    exception_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    exception_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    exception_stack: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)        # AI 诊断结果（markdown 文本）
    diagnosis_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 最近一次诊断时间
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
