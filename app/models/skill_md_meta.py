from datetime import datetime
from sqlalchemy import BigInteger, String, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class SkillMdMeta(Base):
    __tablename__ = "skill_md_meta"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    skill_key: Mapped[str] = mapped_column(String(128), unique=True)
    full_md_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    bind_tools: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    update_time: Mapped[datetime | None] = mapped_column(DateTime, onupdate=func.now(), nullable=True)
