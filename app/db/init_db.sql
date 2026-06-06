-- 越群山智能生活助手 - 数据库初始化脚本
-- 每次启动自动执行，IF NOT EXISTS 保证幂等安全

-- 1. 对话历史表
CREATE TABLE IF NOT EXISTS root.chat_history (
    id          BIGSERIAL PRIMARY KEY,
    session_id  VARCHAR(64) NOT NULL,
    role        VARCHAR(20) NOT NULL,          -- user / ai / system
    content     TEXT        NOT NULL,
    chat_id     VARCHAR(64),
    create_time TIMESTAMP   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_history_session_id ON root.chat_history(session_id);

-- 2. 提示词模板表
CREATE TABLE IF NOT EXISTS root.prompt_templates (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    category    VARCHAR(50)  NOT NULL,         -- report / travel / code / common
    content     TEXT         NOT NULL,          -- 支持 {{变量}} 占位符
    status      SMALLINT     NOT NULL DEFAULT 1, -- 0禁用 1启用
    create_time TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- 3. 技能配置表
CREATE TABLE IF NOT EXISTS root.skills (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    "desc"      TEXT,                           -- 技能功能描述
    skill_type  VARCHAR(30)  NOT NULL,          -- builtin / custom / mcp
    mcp_id      BIGINT,                         -- 绑定 MCP 服务 ID
    timeout     INT          NOT NULL DEFAULT 30, -- 调用超时时间(秒)
    status      SMALLINT     NOT NULL DEFAULT 1, -- 0禁用 1启用
    create_time TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- 4. MCP 服务配置表
CREATE TABLE IF NOT EXISTS root.mcp_config (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    url         VARCHAR(255) NOT NULL,
    auth_type   VARCHAR(30)  NOT NULL,          -- none / api_key / token
    api_key     VARCHAR(255),
    timeout     INT          NOT NULL DEFAULT 20, -- 请求超时时间(秒)
    status      SMALLINT     NOT NULL DEFAULT 1, -- 0禁用 1启用
    create_time TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- 5. MCP 调用日志表
CREATE TABLE IF NOT EXISTS root.mcp_call_log (
    id           BIGSERIAL PRIMARY KEY,
    mcp_id       BIGINT,
    service_name VARCHAR(100),
    status       VARCHAR(30),
    result       TEXT,
    create_time  TIMESTAMP DEFAULT NOW()
);

-- 6. 用户画像表
CREATE TABLE IF NOT EXISTS root.user_profile (
    id          BIGSERIAL PRIMARY KEY,
    user_id     VARCHAR(64) NOT NULL,
    key         VARCHAR(128) NOT NULL,
    value       JSONB NOT NULL,
    source      VARCHAR(64),
    confidence  FLOAT DEFAULT 1.0,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, key)
);
CREATE INDEX IF NOT EXISTS idx_user_profile_user ON root.user_profile(user_id);
CREATE INDEX IF NOT EXISTS idx_user_profile_key ON root.user_profile(key);

-- 7. 对话摘要表
CREATE TABLE IF NOT EXISTS root.conversation_summary (
    id              BIGSERIAL PRIMARY KEY,
    user_id         VARCHAR(64),
    session_id      VARCHAR(64),
    summary         TEXT NOT NULL,
    summary_version INTEGER DEFAULT 1,
    message_count   INTEGER DEFAULT 0,
    token_estimate  INTEGER DEFAULT 0,
    updated_at      TIMESTAMP DEFAULT NOW()
);

-- 8. 执行日志表
CREATE TABLE IF NOT EXISTS root.execution_log (
    id          BIGSERIAL PRIMARY KEY,
    session_id  VARCHAR(64),
    chat_id     VARCHAR(64),
    event_type  VARCHAR(20),
    name        VARCHAR(100),
    status      VARCHAR(10),
    content     TEXT,
    cost_ms     BIGINT,
    create_time TIMESTAMP DEFAULT NOW()
);

-- 9. 文件信息表
CREATE TABLE IF NOT EXISTS root.file_info (
    id              BIGSERIAL PRIMARY KEY,
    file_name       VARCHAR(255) NOT NULL,
    file_path       VARCHAR(512) NOT NULL,
    file_size       BIGINT,
    file_type       VARCHAR(50),
    file_extension  VARCHAR(20),
    chat_id         VARCHAR(64),
    session_id      VARCHAR(64),
    created_by      VARCHAR(100),
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);
