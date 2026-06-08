import os
import urllib.parse
from dotenv import load_dotenv

load_dotenv()


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
    MILVUS_VECTOR_DIM: int = int(os.getenv("MILVUS_VECTOR_DIM", "1024"))

    # Model
    CHAT_MODEL: str = os.getenv("CHAT_MODEL", "qwen3.7-plus")
    REASONING_MODEL: str = os.getenv("REASONING_MODEL", "deepseek-v4-pro")
    INTENT_MODEL: str = os.getenv("INTENT_MODEL", "qwen-turbo")
    MEMORY_SUMMARY_MODEL: str = os.getenv("MEMORY_SUMMARY_MODEL", "qwen-plus")

    # Embedding
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-v4")
    EMBEDDING_DIM: int = int(os.getenv("EMBEDDING_DIM", "1024"))

    # User
    DEFAULT_USER_ID: str = os.getenv("DEFAULT_USER_ID", "admin")

    # Redis
    REDIS_URI: str = os.getenv("REDIS_URI", "http://localhost:6379")
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")

    # App
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

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
