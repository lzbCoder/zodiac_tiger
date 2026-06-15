from datetime import datetime
from sqlalchemy import BigInteger, String, SmallInteger, Integer, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class SkillInfo(Base):
    __tablename__ = "skill_info"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    skill_key: Mapped[str] = mapped_column(String(128), unique=True)
    origin_name: Mapped[str] = mapped_column(String(256))
    skill_name: Mapped[str] = mapped_column(String(256))
    origin_desc: Mapped[str] = mapped_column(Text)
    skill_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    folder_abs_path: Mapped[str] = mapped_column(String(512))
    enable_status: Mapped[int] = mapped_column(SmallInteger, default=1)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    update_time: Mapped[datetime | None] = mapped_column(DateTime, onupdate=func.now(), nullable=True)
