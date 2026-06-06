from datetime import datetime
from sqlalchemy import BigInteger, String, Text, SmallInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PromptTemplate(Base):
    __tablename__ = "prompt_templates"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[int] = mapped_column(SmallInteger, default=1)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
