from pydantic import BaseModel


class SkillSave(BaseModel):
    id: int | None = None
    name: str
    desc: str | None = None
    skill_type: str = "custom"
    mcp_id: int | None = None
    timeout: int = 30
    status: int = 1


class SkillStatus(BaseModel):
    id: int
    status: int
