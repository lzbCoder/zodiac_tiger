from abc import ABC, abstractmethod
from typing import Any


class BaseSkill(ABC):
    """技能基类，所有内置技能和自定义技能均继承此类。"""

    def __init__(self, skill_id: int, name: str, description: str = ""):
        self.skill_id = skill_id
        self.name = name
        self.description = description

    @abstractmethod
    async def execute(self, **kwargs) -> dict[str, Any]:
        """执行技能，返回结果字典。"""
        ...
