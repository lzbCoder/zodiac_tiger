# 越群山智能生活助手 - 后端服务

基于 FastAPI + LangGraph 的多 Agent 智能生活助手后端。

## 技术栈

- **框架**: FastAPI + Uvicorn
- **AI**: LangGraph 多 Agent 编排 + 阿里百炼 qwen3-max (LangChain)
- **数据库**: PostgreSQL (结构化数据) + Milvus (向量检索) + Redis (缓存)
- **日志**: Loguru 统一日志管理
- **依赖管理**: UV

## 目录结构

```
tiger/
├── main.py              # 应用入口
├── pyproject.toml       # UV 依赖管理
├── .env                 # 环境配置
├── logs/                # 日志文件
├── app/
│   ├── api/             # API 路由层
│   ├── agents/          # LangGraph 多 Agent 核心
│   ├── skills/          # 技能注册中心
│   ├── mcp/             # MCP 客户端网关
│   ├── db/              # 数据库连接 + init_db.sql
│   ├── models/          # Pydantic 数据模型
│   ├── services/        # 业务服务层
│   └── utils/           # 工具类 (日志/响应)
```

## 环境要求

- Python 3.12+
- PostgreSQL、Milvus、Redis 实例

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量 (编辑 .env 文件)
# 必填: DASHSCOPE_API_KEY, PG_*, MILVUS_*, REDIS_*

# 3. 启动服务
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 4. 访问 API 文档
# http://localhost:8000/docs
```

## API 接口

| 模块 | 路径 | 说明 |
|------|------|------|
| 聊天 | POST /api/chat/stream | SSE 流式对话 |
| 聊天 | GET /api/chat/history | 对话历史 |
| 模板 | GET/POST /api/template/* | 提示词模板 CRUD |
| 技能 | GET/POST /api/skill/* | 技能管理 |
| MCP | GET/POST /api/mcp/* | MCP 服务管理 |
| 任务 | GET /api/task/list | 任务记录查询 |
| 文件 | GET /api/file/* | 文件列表/下载 |
