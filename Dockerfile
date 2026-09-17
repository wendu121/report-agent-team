# Dockerfile · TD-006 后端服务镜像（推荐默认 D1: python:3.13-slim）
FROM python:3.13-slim

WORKDIR /app

# 优先复制依赖清单，利用镜像层缓存（依赖变更才重装）
COPY requirements.txt .

# 构建期可指定 PyPI 镜像源：国内直连默认 PyPI 极慢（实测 ~70s/包，整层可跑 40min+），
# 传 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple 可提速 ~30x。
# 默认官方源，保证可移植性；不改 Dockerfile 逻辑。
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" -r requirements.txt

# 复制项目源码（.dockerignore 已排除 node_modules/.engine_state/outputs/dist/.env 等噪声）
COPY . .

# 运行时输出目录
RUN mkdir -p /app/outputs /app/.engine_state

# 注意：python:3.13-slim 基础镜像已移除 /bin/sh，不再使用 shell entrypoint
# 改为 Python 直接启动（init_database 在容器内单例启动时调一次）

EXPOSE 8000

# D10：容器健康检查命中 /health
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
