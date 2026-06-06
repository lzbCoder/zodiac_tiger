from pydantic import BaseModel


class TemplateSave(BaseModel):
    id: int | None = None
    name: str
    category: str
    content: str
    status: int = 1


class TemplateStatus(BaseModel):
    id: int
    status: int
