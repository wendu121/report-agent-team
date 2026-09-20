# VERIFICATION · 跨会话记忆召回修复（kb_store 关键词兜底）

- **日期**：2026-09-18
- **范围**：`tools/kb_store.py`（召回降级链）+ `tests/test_kb_store_recall.py`（新增）+ `scripts/verify_memory_e2e.py`（新增）
- **触发**：boss 报「目前 agent 没有跨会话记忆」

---

## 1. 根因（实测，非推断）

### 1.1 现象
会话 1 写入的事实，会话 2 用不同措辞提问 → 召回 0 条 → 表现为「完全失忆」。

### 1.2 链路定位
| 层 | 实测结果 | 证据 |
|----|----------|------|
| 表 | `kb.entries` 存在，53 行（52 chat_fact + 1 report_conclusion） | 容器内探针 |
| 向量列 | `embedding vector(1024)` 存在 | `pg_attribute` 查询 |
| 嵌入端点 | **402 Payment Required** | `EMBED_RESULT: None(degraded)` |
| 网关路由 | 请求打到 **channel #9 = SiliconFlow**，model=`BAAI/bge-m3` | new-api 日志 `channel #9, status code: 402` |
| 备选嵌入 | Zhipu `embedding-3` → **429 余额不足请充值**；`BAAI/bge-large-zh-v1.5` → 402 | curl 实测 |
| 网关模型目录 | `/v1/models` 120 个模型**无任何 embedding 模型** | models 列表 |

**结论**：网关侧无可用嵌入能力（两个含 embedding 的渠道均欠费）→ 所有写入 `embedding=NULL`
→ 原降级分支 `content ILIKE '%<整句>%'` 对自然语言查询几乎必然 0 命中 → 记忆层形同虚设。

> 这是**外部资源阻塞**（渠道余额），不是纯代码 bug；但降级实现太弱，放大了后果。

---

## 2. 修改

### 2.1 `tools/kb_store.py`
1. **新增轻量分词 `_tokenize`**：拉丁词（≥2，小写）+ 中文 bigram（"跨境电商"→跨境/境电/电商），
   去重 + 最小停用词。零第三方依赖、确定性、可离线。
2. **新增 `_lexical_retrieve`**：任一 token 命中即候选（参数化 `ILIKE` OR，不拼原始 query），
   按命中 token 数排序，score = 命中数 / query token 数。
3. **`kb_retrieve` 降级链改为**：vector 检索 → （向量列空或嵌入失败）关键词召回 → 空。
4. **`embed()` 支持 `EMBEDDING_DIMENSIONS`**：显式透传 `dimensions`，便于切换到支持指定维度的
   提供方（如智谱 `embedding-3` 可输出 1024，与既有 `vector(1024)` 列对齐）而无需改表。

### 2.2 新增测试/脚本
- `tests/test_kb_store_recall.py`：6 条单测（分词、去重、停用、打分排序、空查询、参数化）。
- `scripts/verify_memory_e2e.py`：端到端验证（写入→召回→注入 prompt）。

**未改**：表结构、现有向量路径、`chat_agent.py`、`chat_learning.py`。

---

## 3. 验证证据（真实运行）

### 3.1 单测（6/6）
```
PASS test_lexical_retrieve_empty_query_returns_empty
PASS test_lexical_retrieve_scores_and_orders
PASS test_tokenize_cjk_bigrams
PASS test_tokenize_dedup_and_stopwords
PASS test_tokenize_latin_words_lowercased
PASS test_tokenize_query_is_bounded
TOTAL_FAILS: 0   RESULT: PASS
```

### 3.2 跨会话召回（容器内集成）
```
会话1 写入 marker=XSESSION-MARK-4242
会话2 "关税政策怎么样"      -> hits=3  top score 0.6  ✅ 命中
会话2 "东南亚 关税 下调"     -> hits=1  score 1.0      ✅ 命中
会话2 "关税"                -> hits=1  score 1.0      ✅ 命中
会话2 "zzzq 不存在词xyz"     -> hits=0                 ✅ 负对照
RECALL_MARKER_BY_PARAPHRASE: True   RESULT: PASS
```

### 3.3 端到端（写入 → 注入模型 prompt）
```
[session1] kb_write -> 1 (marker=E2E-MEM-dcb317b5)
[session2] intent=chat system_len=4288
[session2] 记忆注入标题出现: True
[session2] 会话1标记出现在 system prompt: True
[negctrl] 无关问题是否误注入: False (期望 False)
RESULT: PASS   EXITCODE=0
```
**证明**：跨会话记忆确实进入模型 system prompt，而非仅落库。

### 3.4 语法/编译
`py_compile` OK；`ast.parse` OK。

### 3.5 镜像重建后仍生效（持久化验证）
修复不止于 `docker cp` 进运行容器——已重建并烘焙进镜像：
- `docker build --build-arg PIP_INDEX_URL=<清华镜像> -t report-agent-team-api:latest` 成功；
- `docker compose up -d api` 重建容器后，`grep -c _lexical_retrieve /app/tools/kb_store.py` → **2**（已烘焙，非临时 cp）；
- 重建容器内重跑 `scripts/verify_memory_e2e.py` → **RESULT: PASS（EXIT=0）**。

> 注：初次 `docker compose up -d --build api` 因 Dockerfile 默认 `PIP_INDEX_URL=https://pypi.org/simple`
> 直连 PyPI 超时而失败；须显式传清华镜像（本项目已知环境坑）。

### 3.6 独立审议（gate-③）
由独立子代理在实容器内复核（读源码 + 跑单测/E2E + 主动构造对抗查询），结论 **PASS_WITH_NOTES**：
- BLOCKER 0 / MAJOR 0（代码级）；
- `%` 注入、SQL 注入、`_` 通配误召 经构造查询**证伪不成立**（F1/F2/F3）；
- 参数编号与顺序一致、降级链一致、调用方契约无破坏（F8）；
- 唯一诚实性标注 **F10（MAJOR，非代码缺陷）**：本修复是**关键词/词面召回**，非语义记忆。
见 `REVIEW_memory_recall.md`。

---

## 4. 诚实边界（未解决项）

| 项 | 状态 | 说明 |
|----|------|------|
| **语义召回**（向量） | ❌ 不可用 | 网关两处 embedding 渠道均欠费（SiliconFlow 402 / Zhipu 429）。**修复需给渠道充值或接入免费嵌入源**（如 Cloudflare Workers AI `@cf/baai/bge-m3`）。 |
| 关键词召回 | ✅ 已生效 | 召回质量低于语义检索：需词面重叠，同义改写（"税" vs "关税"）可能漏召。 |

**交付话术（采纳 gate-③ F10 建议，避免夸大）**：应表述为
「**关键词兜底召回**（跨会话可回忆同词面事实）；**语义召回待嵌入渠道充值后可启用**」，
**不宜**对外称「已具备跨会话语义记忆」——无 token 重叠的改述提问仍会失忆
（例：存「关税下调 3%」、问「进口税费有什么变化」→ 不召回）。
| 历史脏数据 | ⚠️ | 既有 52 条 chat_fact 均 `embedding=NULL`；关键词召回对它们有效。 |

**启用语义召回的配置**（渠道充值后）：`.env` 设 `EMBEDDING_MODEL`（+ 必要时 `EMBEDDING_DIMENSIONS=1024`），
无需改表 —— 只要维度与既有 `vector(1024)` 一致。

---

## 5. 复现命令
```bash
docker exec -w /app report-api python scripts/verify_memory_e2e.py
docker exec -w /app report-api python -c "import asyncio,sys;sys.path.insert(0,'/app');from tools.kb_store import kb_retrieve;print(asyncio.run(kb_retrieve('关税政策',k=3)))"
```
