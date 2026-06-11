from pydantic import BaseModel, ConfigDict


class IntentDisplayOut(BaseModel):
    intent_key: str
    show_name: str
    intent_desc: str
    demo_input: str
    icon: str | None = None
    sort: int
    enable: int
    model_config = ConfigDict(from_attributes=True)


class IntentDisplaySave(BaseModel):
    intent_key: str
    show_name: str | None = None
    intent_desc: str | None = None
    demo_input: str | None = None
    icon: str | None = None
    sort: int | None = None
    enable: int | None = None
