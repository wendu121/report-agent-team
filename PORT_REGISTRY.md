# 端口分配登记册（report-agent-team）

> 建立日期：2026-09-07（TD-007）
> 目的：避免宿主端口撞车。改动任何端口映射前，**先查本表再改**，改后**必须回填本表**。
> 登记原则：只登记「映射到宿主」的端口；容器内部端口（未发布）不占宿主资源，仅在编排内记录。

---

## 一、report-agent-team 宿主端口

| 宿主端口 | 容器:端口 | 服务 | 用途 | 状态 |
|---|---|---|---|---|
| **18080** | report-web:80 | web（nginx:1.27-alpine） | 前端静态站 + `/api` 反向代理（含 WS） | ✅ 现行 |
| ~~8080~~ | ~~report-web:80~~ | web | **已废弃**——与宿主既有容器 `mailhub-web`（8080:80）互斥 | ⛔ 2026-09-07 迁走 |

### 容器内部端口（不发布到宿主）

| 容器:端口 | 服务 | 说明 |
|---|---|---|
| report-api:8000 | api（uvicorn） | D9 决策：不暴露宿主，仅经 web 反代；调试可临时加 `ports: ["8000:8000"]` |
| report-postgres:5432 | postgres | 仅 `report-net` 内网可达，healthcheck `pg_isready` |
| report-redis:6379 | redis | 仅 `report-net` 内网可达，healthcheck `redis-cli ping` |

---

## 二、宿主已占用的其它端口（2026-09-07 实测，选端口时须避开）

| 宿主端口 | 容器 | 用途 |
|---|---|---|
| 3000 | new-api | LLM 多模型网关（`NEWAPI_BASE_URL` 指向此处） |
| 2280–2285 / 5300 | langbot | 机器人服务 |
| 5401 | langbot_plugin_runtime | 插件运行时 |
| 28080 → 8787 | site-monitor | 本地站点监控面板 |
| 54320 → 5432 | memos-pgvector | Memos 知识库数据库 |
| 8096 | tdai-proxy | 代理服务 |
| 8125 / 8424 | tdai-memory-hub | 记忆中心 |
| 8420 | tdai-memory-core | 记忆核心 |
| **8080** | **mailhub-web** | 邮件服务前端（`mailhub` 项目，8080:80）。**现为 Up**（TD-007 迁移后已 `docker start` 复原，实测 HTTP 200），与 report-web 的 18080 互不冲突、同时在线 |

---

## 三、端口分配规则（后续改动必读）

1. **选端口前先探测**：跑端口占用探测 + `docker ps -a --format "{{.Names}}|{{.Ports}}"`，确认候选端口在两张表里都不存在。
2. **优先区间**：`18xxx`（18080、18081…）——与既有 8xxx / 2xxxx / 5xxxx 全不冲突。
3. **改完必回填**：修改 `docker-compose.yml` 端口映射后，同步更新本表第一节，并在 `VERIFICATION.md` 留痕。
4. **冲突处理**：端口冲突属**环境冲突**，非代码缺陷——如实标注，不掩饰为「已通过」；但也不要为了跑通而随意停掉别人的容器（此前为验证临时 `docker stop mailhub-web`，事后须 `docker start mailhub-web` 复原）。
