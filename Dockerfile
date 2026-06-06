FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 UV
RUN pip install uv

# 复制依赖文件
COPY pyproject.toml uv.lock* ./

# 安装 Python 依赖
RUN uv sync --frozen

# 复制应用代码
COPY . .

# 创建日志目录
RUN mkdir -p logs

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
