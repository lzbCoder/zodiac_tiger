from sqlalchemy import BigInteger, String, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class McpToolInfo(Base):
    __tablename__ = "mcp_tool_info"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mcp_key: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(128))
    tool_desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_allow: Mapped[int] = mapped_column(SmallInteger, default=1)
