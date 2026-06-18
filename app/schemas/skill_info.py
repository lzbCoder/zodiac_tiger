from pydantic import BaseModel


class SkillEdit(BaseModel):
    skill_key: str
    display_name: str
    display_desc: str | None = None


class SkillStatus(BaseModel):
    skill_key: str
    enable_status: int


class AgentSkillBind(BaseModel):
    skill_key: str
    agent_codes: list[str]
