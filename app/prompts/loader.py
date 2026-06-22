"""YAML Prompt 模板加载器，基于 Jinja2 Environment + auto_reload。

修改 YAML 文件后立即生效，无需重启服务。
"""

import pathlib
from typing import Any

import yaml
from jinja2 import BaseLoader, Environment, TemplateNotFound
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage

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
    """渲染指定名称的 YAML prompt 模板（template 字段），返回最终字符串。修改 YAML 后自动重载。"""
    return _env.get_template(name).render(**kwargs)


def render_messages(name: str, **kwargs: Any) -> list[BaseMessage]:
    """渲染角色化模板：读取 YAML 的 system / user 字段，返回 [SystemMessage, HumanMessage]。

    - 仅有 system 时（如 planner），返回 [SystemMessage]，由调用方再拼接 scratchpad；
    - system / user 内容均走 jinja 渲染，修改 YAML 后自动生效（每次重新读取文件）。
    """
    yaml_path = _PROMPT_DIR / f"{name}.yaml"
    if not yaml_path.exists():
        raise TemplateNotFound(name)
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}

    messages: list[BaseMessage] = []
    system_tpl = data.get("system")
    user_tpl = data.get("user")
    if system_tpl:
        messages.append(SystemMessage(_env.from_string(system_tpl).render(**kwargs)))
    if user_tpl:
        messages.append(HumanMessage(_env.from_string(user_tpl).render(**kwargs)))
    return messages
