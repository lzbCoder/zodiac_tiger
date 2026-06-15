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

-- 4. 用户画像表
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

-- 10. 用户侧意图能力展示配置表
CREATE TABLE IF NOT EXISTS root.intent_display_config (
    id          BIGSERIAL PRIMARY KEY,
    intent_key  VARCHAR(64)  NOT NULL UNIQUE,
    show_name   VARCHAR(64)  NOT NULL,
    intent_desc TEXT         NOT NULL,
    demo_input  TEXT         NOT NULL,
    icon        VARCHAR(64),
    sort        INT          NOT NULL DEFAULT 0,
    enable      SMALLINT     NOT NULL DEFAULT 1,
    create_time TIMESTAMP    NOT NULL DEFAULT NOW(),
    update_time TIMESTAMP
);

INSERT INTO root.intent_display_config (intent_key, show_name, intent_desc, demo_input, icon, sort, enable)
VALUES
    ('travel', '🗺️ 智能旅游规划', '自定义出行人数、预算、游玩天数、出发与目的城市，自动结合天气、路线规划完整行程，支持导出PDF/Word文档', '2个人从郑州出发去青岛玩4天，预算6000元，帮我规划详细行程并生成文档', 'map', 1, 1),
    ('report', '📊 智能数据分析报表', '上传Excel、CSV数据文件，自动完成数据清洗、统计研判、图表生成、问题分析，输出专业报告并导出文件', '分析上传的销售表格，找出近3个月销量下滑原因，生成Word报告', 'chart', 2, 1),
    ('chat', '💬 通用智能问答', '支持文案写作、公文撰写、知识查询、思路梳理、日常咨询等全场景通用对话', '帮我写一份简洁的月度工作小结', 'chat', 3, 1),
    ('assistant', '🤖 综合智能助手', '处理报表、旅游之外的各类问答、文案撰写、知识咨询、事务协助等综合需求', '帮我梳理一份工作思路 / 解释一下分布式概念', 'assistant', 4, 1)
ON CONFLICT (intent_key) DO NOTHING;
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

-- ==================== MCP 服务管理（新增 3 张表）====================

-- 远程 MCP 服务主配置表
CREATE TABLE IF NOT EXISTS root.mcp_server_config (
    id              BIGSERIAL PRIMARY KEY,
    mcp_key         VARCHAR(128) NOT NULL UNIQUE,
    display_name    VARCHAR(256) NOT NULL,
    endpoint_url    VARCHAR(512) NOT NULL,
    auth_headers    JSONB        NOT NULL DEFAULT '{}',
    enable_status   SMALLINT     NOT NULL DEFAULT 1,
    connect_status  SMALLINT     NOT NULL DEFAULT 0,
    last_check_time TIMESTAMP    NULL,
    remark          VARCHAR(512) NULL,
    create_time     TIMESTAMP    NOT NULL DEFAULT NOW(),
    update_time     TIMESTAMP    NULL
);

-- MCP 工具清单 & 白名单表
CREATE TABLE IF NOT EXISTS root.mcp_tool_info (
    id           BIGSERIAL PRIMARY KEY,
    mcp_key      VARCHAR(128) NOT NULL,
    tool_name    VARCHAR(128) NOT NULL,
    tool_desc    TEXT         NULL,
    input_schema TEXT         NULL,
    is_allow     SMALLINT     NOT NULL DEFAULT 1,
    UNIQUE (mcp_key, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_mcp_tool_mcp_key ON root.mcp_tool_info(mcp_key);

-- Agent-MCP 绑定关系表
CREATE TABLE IF NOT EXISTS root.agent_mcp_rel (
    id         BIGSERIAL PRIMARY KEY,
    agent_code VARCHAR(64)  NOT NULL,
    mcp_key    VARCHAR(128) NOT NULL,
    UNIQUE (agent_code, mcp_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_mcp_agent ON root.agent_mcp_rel(agent_code);

-- ==================== 技能管理（3 张表）====================

-- 技能主表（发现阶段仅读此表，零磁盘 IO）
CREATE TABLE IF NOT EXISTS root.skill_info (
    id               BIGSERIAL PRIMARY KEY,
    skill_key        VARCHAR(128) NOT NULL UNIQUE,
    origin_name      VARCHAR(256) NOT NULL,
    skill_name       VARCHAR(256) NOT NULL,
    origin_desc      TEXT         NOT NULL,
    skill_desc       TEXT         NULL,
    folder_abs_path  VARCHAR(512) NOT NULL,
    enable_status    SMALLINT     NOT NULL DEFAULT 1,
    sort             INT          NOT NULL DEFAULT 0,
    create_time      TIMESTAMP    NOT NULL DEFAULT NOW(),
    update_time      TIMESTAMP    NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_info_enable ON root.skill_info(enable_status);

-- SKILL.md 结构化缓存表（激活阶段懒加载写入）
CREATE TABLE IF NOT EXISTS root.skill_md_meta (
    id               BIGSERIAL PRIMARY KEY,
    skill_key        VARCHAR(128) NOT NULL UNIQUE,
    full_md_content  TEXT         NULL,
    system_prompt    TEXT         NULL,
    bind_tools       TEXT         NULL,
    input_schema     TEXT         NULL,
    output_rule      TEXT         NULL,
    update_time      TIMESTAMP    NULL
);

-- Agent-技能多对多绑定
CREATE TABLE IF NOT EXISTS root.agent_skill_rel (
    id               BIGSERIAL PRIMARY KEY,
    agent_code       VARCHAR(64)  NOT NULL,
    skill_key        VARCHAR(128) NOT NULL,
    UNIQUE (agent_code, skill_key)
);
CREATE INDEX IF NOT EXISTS idx_agent_skill_agent ON root.agent_skill_rel(agent_code);
