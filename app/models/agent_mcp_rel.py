from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class AgentMcpRel(Base):
    __tablename__ = "agent_mcp_rel"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_code: Mapped[str] = mapped_column(String(64))
    mcp_key: Mapped[str] = mapped_column(String(128))
