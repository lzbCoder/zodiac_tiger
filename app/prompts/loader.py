"""YAML Prompt 模板加载器，基于 Jinja2 Environment + auto_reload。

修改 YAML 文件后立即生效，无需重启服务。
"""

import pathlib
from typing import Any

import yaml
from jinja2 import BaseLoader, Environment, TemplateNotFound

_PROMPT_DIR = pathlib.Path(__file__).parent


class _YamlPromptLoader(BaseLoader):
    """从 YAML 文件读取 template 字段，支持 mtime 变更检测。"""

    def __init__(self, path: pathlib.Path) -> None:
        self._path = path

    def get_source(self, _: Environment, template: str):
        yaml_path = self._path / f"{template}.yaml"
        if not yaml_path.exists():
            raise TemplateNotFound(template)
        mtime = yaml_path.stat().st_mtime
        source = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))["template"]
        return source, str(yaml_path), lambda: yaml_path.stat().st_mtime == mtime


_env = Environment(
    loader=_YamlPromptLoader(_PROMPT_DIR),
    auto_reload=True,
    keep_trailing_newline=True,
)


def render(name: str, **kwargs: Any) -> str:
    """渲染指定名称的 YAML prompt 模板，返回最终字符串。修改 YAML 后自动重载。"""
    return _env.get_template(name).render(**kwargs)
