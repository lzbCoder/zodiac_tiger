from datetime import datetime
from sqlalchemy import BigInteger, Integer, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class TaskArtifact(Base):
    """任务产物（大模型工作成果），支持 parent_artifact_id 版本树 v1→v2→v3。"""
    __tablename__ = "task_artifacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    artifact_id: Mapped[str] = mapped_column(String(64), unique=True)
    task_id: Mapped[str] = mapped_column(String(64))
    parent_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_type: Mapped[str | None] = mapped_column(String(50), nullable=True)   # md/docx/pdf/xlsx/text
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_id: Mapped[str | None] = mapped_column(String(64), nullable=True)         # 关联 file_info
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
