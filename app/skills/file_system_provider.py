"""文件系统 SkillProvider 实现：基于 SKILLS_ROOT/{skill_id}/ 目录读取技能文件。"""

from pathlib import Path

from agentskills_core import SkillProvider, SkillNotFoundError, ResourceNotFoundError
from agentskills_core import split_frontmatter


class FileSystemSkillProvider(SkillProvider):
    """从 {skills_root}/{skill_id}/ 目录读取 SKILL.md 和附属资源。"""

    def __init__(self, skills_root: str | Path):
        self._root = Path(skills_root)

    def _skill_md(self, skill_id: str) -> Path:
        return self._root / skill_id / "SKILL.md"

    def _skill_dir(self, skill_id: str) -> Path:
        return self._root / skill_id

    async def get_metadata(self, skill_id: str) -> dict:
        """实现抽象基类要求的 get_metadata，返回 SKILL.md frontmatter dict。"""
        p = self._skill_md(skill_id)
        if not p.exists():
            raise SkillNotFoundError(skill_id)
        frontmatter, _ = split_frontmatter(p.read_text(encoding="utf-8"))
        return frontmatter

    async def get_frontmatter(self, skill_id: str) -> dict:
        """get_metadata 的语义别名，供内部代码使用。"""
        return await self.get_metadata(skill_id)

    async def get_body(self, skill_id: str) -> str:
        p = self._skill_md(skill_id)
        if not p.exists():
            raise SkillNotFoundError(skill_id)
        _, body = split_frontmatter(p.read_text(encoding="utf-8"))
        return body

    async def get_script(self, skill_id: str, name: str) -> bytes:
        p = self._skill_dir(skill_id) / "scripts" / name
        if not p.exists():
            raise ResourceNotFoundError(f"script:{skill_id}/{name}")
        return p.read_bytes()

    async def get_asset(self, skill_id: str, name: str) -> bytes:
        p = self._skill_dir(skill_id) / "assets" / name
        if not p.exists():
            raise ResourceNotFoundError(f"asset:{skill_id}/{name}")
        return p.read_bytes()

    async def get_reference(self, skill_id: str, name: str) -> bytes:
        p = self._skill_dir(skill_id) / "references" / name
        if not p.exists():
            raise ResourceNotFoundError(f"reference:{skill_id}/{name}")
        return p.read_bytes()
