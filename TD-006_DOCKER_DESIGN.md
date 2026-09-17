# TD-006 · Docker 容器化改造设计文档

> **状态**：草案（待评审门禁）。评审 PASS 前**禁止**编写 Dockerfile / compose / nginx.conf 等成品代码。
> **登记**：TECH_DEBT.md · TD-006（2026-09-07）
> **基线**：不破坏 `e4b01d3`；本任务作为新增文件 / 新增提交叠加。
> **纪律**：设计评审 → 编码 → `docker build` 验证 → edict-gate 三道门禁全 PASS → commit。严禁跳过评审/门禁、严禁直接落地成品 Docker 代码。

---

## 0. 摘要（Executive Summary）

report-agent-team 当前**已有一份不完整的 Docker 配置**（`Dockerfile` 453B、`docker-compose.yml` 1607B，均为 9-05 M6 产物），但实测存在多处会导致容器**启动即崩溃或功能残缺**的硬伤。本任务不是从零造 Docker，而是**补齐并修正**现有配置，使其能真实跑通「前端 Nginx 静态容器 + 后端 FastAPI 容器 + Postgres + Redis」的完整栈。

**最关键的三个真实缺口（经读码实证，非推测）**：
1. `requirements.txt` 未含 `sqlalchemy` / `asyncpg` / `redis`，但 `server/database.py:6-8` `import sqlalchemy`、`:122` `import redis.asyncio` → 镜像 `pip install` 成功但容器运行 `ImportError` 崩溃。
2. `server/main.py` 的 `lifespan` 未调用 `init_database()` → 表不会自动创建 → 首次访问 DB 即错。
3. `docker-compose.yml` 的 `api` 服务**未注入 `NEWAPI_BASE_URL` / `NEWAPI_API_KEY`** → 引擎子进程（server/engine_runner.py:47-49 读这两个变量）连不上 new-api 网关 → 研报永不产出。

---

## 1. 现状核查（事实基础，全部经读码/读配置确认）

| 项 | 现状 | 证据 | 结论 |
|---|---|---|---|
| 后端入口 | `uvicorn server.main:app --host 0.0.0.0 --port 8000` | server/main.py:65-73、Dockerfile:25 | 端口 8000 固定 |
| 后端依赖清单 | `langgraph/openai/pyyaml/fastapi/uvicorn[standard]/websockets/pydantic/httpx` | requirements.txt:1-19 | **缺 sqlalchemy/asyncpg/redis（致命）** |
| 持久层实际导入 | `from sqlalchemy import create_engine`；`from sqlalchemy.ext.asyncio import ...`；`import redis.asyncio as aioredis` | server/database.py:6-8、:122 | 与 requirements 不符 |
| 数据库 URL | `DATABASE_URL`(同步) / `ASYNC_DATABASE_URL`(asyncpg) 经 `os.getenv` 读，默认 localhost | server/database.py:20-32 | compose 已传，OK |
| Redis | `REDIS_URL` 在 compose 声明，但 `database.py` `RedisClient` 默认硬编码 `redis://localhost:6379/0`，**未读 `REDIS_URL`** | server/database.py:132、docker-compose.yml:51 | **变量声明但未消费** |
| 引擎 LLM | `engine_runner.py` 子进程读 `NEWAPI_BASE_URL`/`NEWAPI_API_KEY`（默认 localhost:3000/v1、sk-no-key），构造 `NewApiLLMClient` 传入 `run_report` | server/engine_runner.py:47-49、:54-59 | 容器必须注入，否则无真实研报 |
| 检索 | `TAVILY_API_KEY` 缺失 → `build_tools()` 降级 MockProvider（占位，不冒充真实） | tools/__init__.py:57-72 | 可选变量 |
| 表初始化 | `init_database()` 存在但 `main.py` `lifespan` 未调用 | server/main.py:13-18、database.py:88-91 | **启动不建表** |
| 前端构建 | `npm run build` = `vue-tsc -b && vite build`，产物 `web/dist/`（默认 outDir） | web/package.json:8、vite.config.ts | 多阶段构建 |
| 前端 API 调用 | `axios` `baseURL: '/api/v1'`（相对）；WS 用 `location.host` 自动 ws/wss | web/src/api/client.ts:9、wsService.ts:20-21 | **无硬编码后端地址**，Nginx 反代 `/api` 即可 |
| 前端代理(dev) | vite `server.proxy['/api']` → `VITE_API_TARGET`（默认 localhost:8000），`ws:true` | web/vite.config.ts:30-40 | 生产由 Nginx 接管 |
| 现有 compose | postgres:15-alpine + redis:7-alpine + api（**无前端**）；api 无 NEWAPI_*、无 healthcheck、密码硬编码 | docker-compose.yml | 需补全 |
| .dockerignore | **不存在** → 现有 `Dockerfile` `COPY . .` 会把 node_modules/.vite/.engine_state/outputs 塞进镜像 | 目录列表 | 必须新增 |
| .gitignore | 已忽略 `.env`/`*.key`/`node_modules/`/`.vite/`/`*.tsbuildinfo`/`dist/`/`.engine_state/`/`outputs/` | .gitignore | 密钥与噪声已隔离 |

> 说明：`MODE`（`orchestrator.py:1064` 默认 `stub`）**仅影响直接 `python orchestrator.py` CLI 运行**，server 经 `engine_runner` 调 `run_report` 始终用 `NEWAPI_*` 真实客户端。故容器内 `MODE` 无意义，真实研报取决于 `NEWAPI_*` 注入，而非 `MODE`。

---

## 2. 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│ 浏览器 (host:8080)                                            │
└───────────────┬─────────────────────────────────────────────┘
                │  HTTP / WS
                ▼
┌──────────────────────────┐         /api (REST+WS 反代)
│ web (nginx:alpine :80)    │ ───────────────────┐
│  静态资源 /usr/share/nginx│                    │
│  html + 反代 /api         │                    │
└──────────────────────────┘                    │
                                                 ▼
┌──────────────────────────┐    ┌────────────────────────────┐
│ api (python:3.13-slim)   │    │ postgres:15-alpine          │
│  uvicorn :8000           │───▶│  report_agent_team          │
│  引擎子进程(继承 env)     │    └────────────────────────────┘
└──────────────────────────┘    ┌────────────────────────────┐
       │                        │ redis:7-alpine (:6379)      │
       └────────────────────────│  状态缓存(可选)             │
                                └────────────────────────────┘
```

- **网络**：自定义 bridge 网络（如 `report-network`）；postgres/redis **不暴露宿主机端口**（仅内部互通），api 仅内部 :8000，web 暴露 `8080:80` 给宿主。
- **卷**：`postgres_data`、`redis_data` 持久化；api 挂载 `./outputs`、` ./.engine_state`（运行时产物，gitignored）。

---

## 3. 后端容器（FastAPI）

### 3.1 基础镜像选型（D1）
- **推荐 `python:3.13-slim`**（Debian bookworm）。理由：与本地运行时 3.13.12 一致；`asyncpg`/`psycopg` 在 Debian 有预编译 wheel，`pip install` 即装即用。
- 不推荐 `python:3.13-alpine`：`asyncpg`/`greenlet` 在 musl 下需现场编译（缺 build-base/gcc），构建慢且易失败。
- **多阶段可选**：用 `python:3.13-slim` 作为唯一阶段即可（pip 多为 wheel，层缓存友好）；若追求更小体积，可加构建阶段编译纯 Python wheel 再拷入 `python:3.13-slim` 运行阶段，但收益有限，**本期从简**。

### 3.2 依赖修正（D3 · 致命缺口）
`requirements.txt` 须新增（并建议 pin 版本以保证可复现）：
```
sqlalchemy>=2.0
asyncpg>=0.29
redis>=5.0
```
> 注：`redis.asyncio` 在 `redis>=4.2` 即存在，`redis>=5.0` 稳妥。修改 requirements 属引擎侧代码变更，编码阶段一并提交，并触发现有门禁（typecheck 仅前端，故后端依赖变更靠 `docker build` + 启动探测验证）。

### 3.3 启动入口与建表（D4 · 高缺口）
新增 `server/entrypoint.sh`（或 `docker-entrypoint.sh`）：
```sh
#!/bin/sh
set -e
echo "等待数据库就绪..."
# 简单等待 postgres 监听（或用 wait-for-it / pg_isready）
python - <<'PY'
from server.database import init_database
init_database()   # Base.metadata.create_all —— 首次启动建表
PY
exec uvicorn server.main:app --host 0.0.0.0 --port 8000
```
- compose 的 `api.command` 改为 `["sh","/app/server/entrypoint.sh"]`（或 Dockerfile `ENTRYPOINT`）。
- 风险：无迁移工具（Alembic 未引入），`create_all` 仅建表不处理 schema 变更；本期表结构稳定，可接受。若后续改模型需手工迁移——记 TD（非本期）。

### 3.4 .dockerignore（D6 · 必须）
后端 `.dockerignore`：
```
__pycache__/
*.py[cod]
.pytest_cache/
.venv/
node_modules/
web/node_modules/
.vite/
.editor_state/
outputs/
.engine_state/
.git/
.env
*.key
*.pem
tests/
*.md
dist/
```
> 确保 `COPY . .` 只带源码 + requirements + config，不带噪声与运行时 dump，且无密钥泄露。

---

## 4. 前端容器（Nginx 静态）

### 4.1 多阶段构建（D2）
```dockerfile
# ---- 构建阶段 ----
FROM node:22-alpine AS build
WORKDIR /app
COPY web/package*.json ./
RUN npm ci
COPY web/ .
RUN npm run build          # vue-tsc -b && vite build → dist/

# ---- 运行阶段 ----
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```
- 基础镜像 `node:22-alpine`（本地 Node 22.x 一致）、`nginx:1.27-alpine`。
- 构建阶段仅需 `web/` 子目录；`node_modules` 不进最终镜像。
- **无需 `VITE_API_TARGET`**：前端用相对 `/api/v1` + `location.host`，Nginx 反代即可，构建期不依赖后端地址。

### 4.2 nginx.conf（D8 · WS 反代）
要点：静态托管 + `/api` REST/WS 反代 + SPA 回退。
```
server {
  listen 80;
  root /usr/share/nginx/html;
  index index.html;

  location /api/ {
    proxy_pass http://api:8000;          # compose 服务名
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    # WebSocket 必需
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;            # 长任务/WS 不中断
  }

  location / {
    try_files $uri $uri/ /index.html;     # SPA 前端路由回退
  }
}
```
> `proxy_pass http://api:8000` 末尾**不带** `/api`（因 location 已含 `/api`，且前端请求路径为 `/api/v1/...`，需原样转发）。

---

## 5. docker-compose 完整编排（D9/D10）

四类服务：`postgres` / `redis` / `api` / `web`。关键修正点：
1. **新增 `web` 服务**（nginx，暴露 `8080:80`，`depends_on` api）。
2. **`api` 补全环境变量**：`NEWAPI_BASE_URL` / `NEWAPI_API_KEY` / `NEWAPI_MAX_RETRIES` / `NEWAPI_TIMEOUT` / `TAVILY_API_KEY`(可选) / `DATABASE_URL` / `ASYNC_DATABASE_URL` / `REDIS_URL` / `PYTHONUNBUFFERED=1`。
3. **`api` 加 healthcheck**：`curl -f http://localhost:8000/health`（需镜像含 curl，或改用 python 探活；alpine 无 curl → 用 `wget -qO-` 或 python）。
4. **postgres/redis 不暴露宿主机端口**（去掉 `ports:` 或仅内部）；密码从 `.env` 读取，不硬编码。
5. **`REDIS_URL` 接线**（D5）：要么 `database.py` 改读 `os.getenv("REDIS_URL", "redis://localhost:6379/0")`（小代码改动，本期建议做）；要么 compose 删 `REDIS_URL`（redis 当前为可选）。**推荐前者**（接线），属引擎侧小改，编码阶段随 TD-006 一并提交 + 门禁。

> 草案示意（非成品，评审通过后据 D1–D10 落码）：
```yaml
services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-postgres}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-change_me}
      POSTGRES_DB: report_agent_team
    volumes: [postgres_data:/var/lib/postgresql/data]
    healthcheck: { test: ["CMD-SHELL","pg_isready -U ${POSTGRES_USER:-postgres}"], interval: 10s, timeout: 5s, retries: 5 }
  redis:
    image: redis:7-alpine
    healthcheck: { test: ["CMD","redis-cli","ping"], interval: 10s, timeout: 5s, retries: 5 }
  api:
    build: { context: ., dockerfile: Dockerfile }
    environment:
      NEWAPI_BASE_URL: ${NEWAPI_BASE_URL:-http://localhost:3000/v1}
      NEWAPI_API_KEY: ${NEWAPI_API_KEY}
      NEWAPI_MAX_RETRIES: ${NEWAPI_MAX_RETRIES:-2}
      NEWAPI_TIMEOUT: ${NEWAPI_TIMEOUT:-120}
      TAVILY_API_KEY: ${TAVILY_API_KEY:-}
      DATABASE_URL: postgresql://${POSTGRES_USER:-postgres}:${POSTGRES_PASSWORD:-change_me}@postgres:5432/report_agent_team
      ASYNC_DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-postgres}:${POSTGRES_PASSWORD:-change_me}@postgres:5432/report_agent_team
      REDIS_URL: redis://redis:6379/0
      PYTHONUNBUFFERED: "1"
    depends_on: { postgres: { condition: service_healthy }, redis: { condition: service_healthy } }
    volumes: ["./outputs:/app/outputs", "././engine_state:/app/.engine_state"]
  web:
    build: { context: ., dockerfile: web/Dockerfile }
    ports: ["8080:80"]
    depends_on: [api]
volumes: { postgres_data: {}, redis_data: {} }
```

---

## 6. 全部环境变量清单

| 变量 | 作用域 | 默认值 | 必填 | 来源/证据 | 说明 |
|---|---|---|---|---|---|
| `NEWAPI_BASE_URL` | api（引擎子进程继承） | `http://localhost:3000/v1` | **是**（真实研报） | engine_runner.py:47 | new-api 网关 /v1 地址 |
| `NEWAPI_API_KEY` | api | `sk-no-key` | **是** | engine_runner.py:48 | 网关 key；经 `.env` 注入，不入库 |
| `NEWAPI_MAX_RETRIES` | api | `2` | 否 | run_real.py:50 | 单调用重试 |
| `NEWAPI_TIMEOUT` | api | `120` | 否 | run_real.py:51 | 单请求超时(秒) |
| `TAVILY_API_KEY` | api | 空 | 否 | tools/__init__.py:57 | 缺失→MockProvider 占位检索 |
| `DATABASE_URL` | api | `postgresql://...localhost:5432/...` | 是 | database.py:20 | 同步引擎；compose 传 postgres:5432 |
| `ASYNC_DATABASE_URL` | api | `postgresql+asyncpg://...localhost:5432/...` | 是 | database.py:28 | 异步引擎(asyncpg) |
| `REDIS_URL` | api | `redis://localhost:6379/0` | 否 | database.py:132(硬编码未读) | **当前代码未消费**；D5 接线后生效 |
| `MODE` | 仅 CLI | `stub` | 否 | orchestrator.py:1064 | **容器内无意义**（server 路径始终真实） |
| `STUB_SCENARIO` | 仅 CLI | `pass` | 否 | orchestrator.py:1070 | 仅 `python orchestrator.py` stub 用 |
| `PYTHONUNBUFFERED` | api | 未设 | 推荐 | — | 容器日志实时 flush |
| `POSTGRES_USER` | compose(postgres) | `postgres` | 是 | compose | 经 `.env` |
| `POSTGRES_PASSWORD` | compose(postgres) | `change_me` | 是 | compose | **务必改**，经 `.env` 注入 |
| `POSTGRES_DB` | compose(postgres) | `report_agent_team` | 是 | compose | — |
| `VITE_API_TARGET` | web(构建期) | `http://localhost:8000` | 否 | vite.config.ts:6 | **生产不需要**；相对路径+location.host 已覆盖 |
| `REAL_TOPIC` / `REAL_SCOPE` | 仅 CLI(run_real) | — | 否 | run_real.py:78-79 | `mode==real` 直跑路径用，**不在容器 server 范围**，故单列 |

> 前端**无需任何运行时 env**（构建后纯静态）；所有后端寻址由 Nginx 反代完成。

---

## 7. 镜像体积评估

| 镜像 | 基础 | 估算(压缩后) | 依据 |
|---|---|---|---|
| api | python:3.13-slim (~130MB) | **~600–800MB** | langgraph 拉 langchain-core 等重依赖(~100–200MB)；+fastapi/uvicorn/sqlalchemy/asyncpg/redis/openai/pydantic/httpx/websockets/pyyaml。langgraph 版本松散(`>=0.2`)→建议 pin 以控体积与可复现 |
| web(运行) | nginx:1.27-alpine (~45MB) | **~50MB** | 仅 `dist/` 静态资源；build 阶段 node:22-alpine 为临时层不计入 |
| postgres | postgres:15-alpine | ~80MB | 官方镜像 |
| redis | redis:7-alpine | ~30MB | 官方镜像 |
| **全栈** | — | **~800MB–1GB** | api 占绝对主导 |

**优化建议（非强制，记 TD）**：
- langgraph pin 具体版本；评估是否仅依赖 `langgraph-core` 减负。
- 后端多阶段（编译 wheel → slim 运行）可再降 ~50–100MB，本期从简。
- 前端已最优（nginx:alpine + 静态）。

---

## 8. 风险点（R1–R10）

| 级 | 风险 | 证据 | 缓解（设计内已含） |
|---|---|---|---|
| **致命** | requirements 缺 sqlalchemy/asyncpg/redis → 容器 ImportError 崩溃 | database.py:6-8,122 vs requirements.txt | D3 补依赖 |
| **高** | 启动不建表 → 首次 DB 访问失败 | main.py:13-18 未调 init_database | D4 entrypoint 调 init_database |
| **中** | REDIS_URL 声明未消费 → 容器连 localhost redis 失败 | database.py:132 硬编码 | D5 读 REDIS_URL |
| **中** | 无 .dockerignore → 镜像臃肿 + .engine_state/outputs 泄露 | 目录无该文件 | D6 新增 |
| **中** | 密钥管理：NEWAPI_API_KEY / POSTGRES_PASSWORD 不能进镜像/compose | 现状 compose 硬编码密码 | .env(gitignored)+.env.example；compose 用 `${}` 引用 |
| **中** | Nginx WS 反代缺 Upgrade 头/超时 → WS 断流 | wsService.ts 长连 | D8 nginx.conf 配 Upgrade + read_timeout 3600s |
| **低** | CORS `allow_origins=["*"]`+credentials | main.py:32-38 | Nginx 同源反代后前端不再跨域；生产可收紧 |
| **低** | api 无 healthcheck | compose 现状 | D10 加 /health 探活 |
| **低** | langgraph 版本松散 → 可复现差/体积飘 | requirements.txt:3 | pin 版本 |
| **信息** | web/dist、outputs、.engine_state 被 gitignore → 必须容器内构建，勿 COPY 宿主产物 | .gitignore | .dockerignore 排除 + 容器构建 |

---

## 9. 待评审决议（D1–D10）

请 boss 评审并拍板（评审 PASS 后方可编码）：

- **D1** 后端基础镜像：`python:3.13-slim`（推荐）/ `python:3.13-alpine`（需编译 asyncpg，不推荐）。
- **D2** 前端：`node:22-alpine` 构建 → `nginx:1.27-alpine` 运行（推荐）；或单阶段 nginx + 宿主预构建 dist（不推荐，违反「容器内构建」）。
- **D3** requirements 补 `sqlalchemy/asyncpg/redis` 并 pin 版本 —— **必须**（致命缺口）。
- **D4** 启动建表：`entrypoint.sh` 调 `init_database()`（推荐）/ 引入 Alembic（过度，否）。
- **D5** REDIS_URL：`database.py` 改读 `os.getenv("REDIS_URL", "redis://localhost:6379/0")`（推荐，小代码改）/ 删 compose REDIS_URL（redis 转纯可选，否）。
- **D6** `.dockerignore` 内容按 §3.4（必须）。
- **D7** 密钥：`.env`(gitignored) + `.env.example`(提交)，compose 引用 `${}`（必须）。
- **D8** nginx.conf 反代 `/api` 含 WS（必须）。
- **D9** 端口暴露：web `8080:80`、api 内部、postgres/redis 不暴露（推荐）/ 全暴露（否，安全风险）。
- **D10** api healthcheck 命中 `/health`（推荐）。

---

## 10. 验证计划（编码后执行）

1. **后端镜像**：`docker build -t report-api .` → `docker run --rm -e NEWAPI_API_KEY=... -e DATABASE_URL=... -e ASYNC_DATABASE_URL=... report-api` → 观察启动**无 ImportError**、`curl /health` 返回 `healthy`。
2. **前端镜像**：`docker build -t report-web -f web/Dockerfile .` → `docker run --rm -p 8080:80 report-web` → `curl /` 返回 index.html；`curl /api/v1/health` 经 Nginx 反代命中 api。
3. **全栈**：`docker compose up -d --build` → 四服务 `healthy` → 经 `web:8080/api/v1/tasks` 提交任务 → 轮询至 `task_done`（真实研报产出，验证 NEWAPI_* 注入 + 建表 + 反代全链路）。
4. **构建验证 Gate**：上述 `docker build` 全成功 + 启动探测通过。

---

## 11. 门禁纪律（不可跳过）

- 编码阶段产出：`Dockerfile`(重写)、`web/Dockerfile`(新增)、`web/nginx.conf`(新增)、`docker-compose.yml`(重写)、`.dockerignore`(新增)、`.env.example`(新增)、`server/entrypoint.sh`(新增)、`requirements.txt`(补依赖)、`server/database.py`(REDIS_URL 接线，D5)。
- 上述文件在 `e4b01d3` 之上新增/修改，**不 revert 已入库内容**。
- 完成后走 edict-gate 三道闸：
  1. **校验**：前端 `npm run typecheck` + `npm run build` 0 error；`docker build` 成功。
  2. **自审**：回填 `VERIFICATION.md`（§新增 TD-006 构建验证实录）。
  3. **独立审议**：独立子代理 REVIEW.md（严禁主代理自签 `reviewed-by`）。
- 三道闸全 PASS → `git add` / `commit`（基线 `e4b01d3` 之上）。

---

## 12. 范围外（本期不做）

- Alembic 迁移体系（D4 用 create_all 暂代）。
- Element Plus 按需引入（TD-001，前端冻结）。
- TD-002 中间事件流式化（独立任务，设计优先）。
- glm-4.7 映射切回（TD-004，待通道恢复）。
- 多副本 / K8s / CI-CD 流水线（超出单机 compose）。
