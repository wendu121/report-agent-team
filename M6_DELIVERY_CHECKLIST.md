# M6 HTTP/WebSocket 服务层交付清单

> **状态**：✅ 全部完成
> **日期**：2026-09-05
> **里程碑**：M6-1 至 M6-6 全部闭环
> **设计纪律**：先设计后编码，API_SPEC 先冻结后实现

---

## 📊 完成概览

| 阶段 | 内容 | 状态 | 产出文件 |
|------|------|------|----------|
| M6-1 | API_SPEC 评审通过 | ✅ | `API_SPEC.md` (v1.0) |
| M6-2 | FastAPI 服务层实现 | ✅ | `server/main.py`、`api.py`、`websocket.py` |
| M6-3 | 引擎 CLI 封装 | ✅ | `engine_client.py`、`engine_runner.py` |
| M6-4 | 状态持久化集成 | ✅ | `models.py`、`database.py` |
| M6-5 | 审计包生成 | ✅ | `audit_export.py` |
| M6-6 | 部署与测试 | ✅ | `docker-compose.yml`、`Dockerfile`、启动脚本 |

---

## 🎯 核心功能实现

### REST 接口（4 端点）
- `POST /api/v1/tasks` — 创建任务（后台启动引擎进程）
- `GET /api/v1/tasks/{id}` — 获取完整状态投影
- `POST /api/v1/tasks/{id}/review` — 人工复核（confirm/retry/abort）
- `GET /api/v1/tasks/{id}/audit-export` — 下载审计包（ZIP）

### WebSocket 事件流（7 事件）
`agent_complete`、`gate_complete`、`rework_trigger`、`round_update`、`tool_error`、`task_done`、`task_escalated`

### 引擎进程管理
- 每任务一个独立引擎进程（隔离 state）
- 文件系统通信（input.json → output.json）
- 后台任务异步启动引擎（asyncio.create_task）
- 进程生命周期管理（启动/等待/终止）

### 数据持久化
- **PostgreSQL 15**：tasks、routing_states、engine_events、gate_reviews 四表
- **Redis 7**：实时状态缓存、WebSocket 连接管理
- SQLAlchemy 2.0 异步会话

### 审计包生成
- ZIP 包结构：report.md + gate_review_history.json + prior_versions.json + user_task.json + engine_events.json + metadata.json
- UTF-8 编码、ensure_ascii=false
- 完整性验证

---

## 🧪 验证结果

- ✅ **导入验证**：server.main、api、websocket、engine_client、audit_export 全部可导入，无循环依赖
- ✅ **测试套件**：41/41 passed（原有 25 + 回归 16），3.07s 完成
- ✅ **API 启动**：FastAPI 应用实例可创建，CORS + 路由注册正常
- ✅ **文件结构**：13 个核心文件完整，无冗余调试文件

---

## 📁 交付文件清单

### 核心服务层（server/）
```
server/
├── __init__.py          # Python 包初始化
├── main.py              # FastAPI 应用入口（CORS + 路由注册 + 健康检查）
├── api.py               # REST 接口实现（4 端点）
├── websocket.py         # WebSocket 事件推送（7 事件）
├── engine_client.py     # 引擎进程封装（EngineProcess/Manager/StateStore）
├── engine_runner.py     # 引擎运行器（独立进程入口）
├── models.py            # 数据库模型（Task/RoutingState/EngineEvent/GateReview）
├── database.py          # 数据库会话管理（PostgreSQL + Redis）
└── audit_export.py      # 审计包生成器（ZIP 打包）
```

### 部署配置
```
docker-compose.yml       # Docker Compose 配置（PostgreSQL + Redis + FastAPI）
Dockerfile               # 服务层容器镜像
start_server.sh          # Linux/macOS 启动脚本
start_server.ps1         # Windows 启动脚本
requirements.txt         # 依赖更新
```

### 测试与文档
```
test_server.py           # 服务层端到端测试脚本
API_SPEC.md              # v1.0（设计评审完成）
M6_COMPLETION_REPORT.md  # M6 完成报告
```

---

## 🚀 下一步

1. **M7 前端对接**：基于 API_SPEC.md 开发 Vue3 UI
2. **真实 TAVILY key**：补入调研内容，提升研报质量
3. **性能测试**：并发任务处理能力验证