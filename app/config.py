import os
import urllib.parse
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_tiger_root = Path(__file__).parent.parent


class Settings:
    # OpenAI 兼容端点
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

    # 高德地图
    AMAP_API_KEY: str = os.getenv("AMAP_API_KEY", "")

    # Tavily
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # 阿里百炼
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")

    # PostgreSQL
    PG_HOST: str = os.getenv("PG_HOST", "localhost")
    PG_PORT: int = int(os.getenv("PG_PORT", "5432"))
    PG_DATABASE: str = os.getenv("PG_DATABASE", "tiger")
    PG_SCHEMA: str = os.getenv("PG_SCHEMA", "root")
    PG_USER: str = os.getenv("PG_USER", "postgres")
    PG_PASSWORD: str = os.getenv("PG_PASSWORD", "")

    # Milvus
    MILVUS_URI: str = os.getenv("MILVUS_URI", "http://localhost:19530")
    MILVUS_TOKEN: str = os.getenv("MILVUS_TOKEN", "")
    MILVUS_DATABASE: str = os.getenv("MILVUS_DATABASE", "life_agent")
    MILVUS_COLLECTION_ASSISTANT: str = os.getenv("MILVUS_COLLECTION_ASSISTANT", "assistant_vector")
    MILVUS_COLLECTION_EPISODIC: str = os.getenv("MILVUS_COLLECTION_EPISODIC", "episodic_memory")
    MILVUS_COLLECTION_PROCEDURAL: str = os.getenv("MILVUS_COLLECTION_PROCEDURAL", "procedural_memory")
    MILVUS_VECTOR_DIM: int = int(os.getenv("MILVUS_VECTOR_DIM", "1024"))

    # Model
    CHAT_MODEL: str = os.getenv("CHAT_MODEL", "qwen3.7-max")
    REASONING_MODEL: str = os.getenv("REASONING_MODEL", "deepseek-v4-pro")
    INTENT_MODEL: str = os.getenv("INTENT_MODEL", "qwen-turbo")
    # ReAct 规划节点专用：仅做工具决策，用快模型 + 关闭思考，避免单轮规划耗时数十秒
    PLANNER_MODEL: str = os.getenv("PLANNER_MODEL", "qwen3.6-flash")
    MEMORY_SUMMARY_MODEL: str = os.getenv("MEMORY_SUMMARY_MODEL", "qwen-plus")

    # 前端可切换的"最终 AI 回复"模型白名单（仅作用于最终回复节点，执行过程模型不变）
    REPLY_MODELS: list[str] = ["qwen3.7-max", "qwen3.6-flash", "deepseek-v4-pro"]

    # Embedding
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "1024"))

    # Memory tuning
    SUMMARY_TOKEN_THRESHOLD: int = int(os.getenv("SUMMARY_TOKEN_THRESHOLD", "6000"))
    MEMORY_RECALL_LIMIT: int = int(os.getenv("MEMORY_RECALL_LIMIT", "5"))

    # Procedural memory（程序记忆/反思）
    REFLECTION_TURN_INTERVAL: int = int(os.getenv("REFLECTION_TURN_INTERVAL", "10"))  # 每 N 轮触发一次反思
    PROCEDURAL_RECALL_LIMIT: int = int(os.getenv("PROCEDURAL_RECALL_LIMIT", "3"))
    PROCEDURAL_DEDUP_THRESHOLD: float = float(os.getenv("PROCEDURAL_DEDUP_THRESHOLD", "0.90"))  # 相似度≥此值视为同一规则，合并
    PROCEDURAL_DECAY_DAYS: int = int(os.getenv("PROCEDURAL_DECAY_DAYS", "30"))   # 超过此天数未命中则衰减
    PROCEDURAL_DECAY_FACTOR: float = float(os.getenv("PROCEDURAL_DECAY_FACTOR", "0.8"))
    PROCEDURAL_MIN_SCORE: float = float(os.getenv("PROCEDURAL_MIN_SCORE", "0.2"))  # 低于此分失效

    # User
    DEFAULT_USER_ID: str = os.getenv("DEFAULT_USER_ID", "admin")

    # Redis
    REDIS_URI: str = os.getenv("REDIS_URI", "http://localhost:6379")
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")

    # App
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Skills
    SKILLS_ROOT: str = os.getenv("SKILLS_ROOT", str(_tiger_root / "skills"))

    # LangSmith
    LANGCHAIN_TRACING_V2: str = os.getenv("LANGCHAIN_TRACING_V2", "false")
    LANGCHAIN_API_KEY: str = os.getenv("LANGCHAIN_API_KEY", "")
    LANGCHAIN_PROJECT: str = os.getenv("LANGCHAIN_PROJECT", "life-agent")

    @property
    def pg_dsn(self) -> str:
        return (
            f"postgresql://{self.PG_USER}:{urllib.parse.quote_plus(self.PG_PASSWORD)}"
            f"@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DATABASE}"
        )


settings = Settings()
