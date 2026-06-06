from datetime import datetime
from sqlalchemy import BigInteger, String, Text, SmallInteger, Integer, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Skill(Base):
    __tablename__ = "skills"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    desc: Mapped[str | None] = mapped_column("desc", Text, nullable=True)
    skill_type: Mapped[str] = mapped_column(String(30))
    mcp_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    timeout: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[int] = mapped_column(SmallInteger, default=1)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
