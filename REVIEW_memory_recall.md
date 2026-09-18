# Gate-③ 独立审议 · 跨会话记忆召回修复（kb_store.py）

**审查对象**：`tools/kb_store.py`（新增 `_tokenize` / `_lexical_retrieve`）、`tests/test_kb_store_recall.py`、`scripts/verify_memory_e2e.py`
**审查方式**：独立读源码 + 在 `report-api` 实容器内运行单测/E2E + 主动构造对抗查询尝试证伪
**审查者**：independent-subagent (gate-③)
**日期**：2026-09-18

---

## 结论（Verdict）

**PASS_WITH_NOTES**

代码本身正确、参数化安全、单测 6/6 与 E2E 均实测通过，注入担忧经构造查询证伪（不成立）。唯一需要诚实标注的点是「跨会话记忆」的表述**略显夸大**：本修复提供的是**关键词/词面召回**，而非语义记忆；嵌入端点仍 402 不可用， paraphrase（改述）且不与原文共享 token 的查询仍会失忆。这是能力边界问题，非代码 bug。

---

## 一、实测证据（命令 + 输出）

### 1. 单测（容器内 stdlib runner）
```
> docker exec -w /app report-api python -c "import sys,importlib.util;sys.path.insert(0,'/app');spec=importlib.util.spec_from_file_location('t','tests/test_kb_store_recall.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);[getattr(m,n)() for n in dir(m) if n.startswith('test_')];print('UNIT_OK')"
UNIT_OK
```
→ 6 个 `test_*` 全部执行无异常，打印 `UNIT_OK`。**与「6/6 通过」声明一致。**

### 2. E2E（真实 DB，嵌入端点 402）
```
> docker exec -w /app report-api python scripts/verify_memory_e2e.py
[session1] kb_write -> 1 (marker=E2E-MEM-503d7426)
[session2] intent=chat system_len=3686
[session2] 记忆注入标题出现: True
[session2] 会话1标记出现在 system prompt: True
[negctrl] 无关问题是否误注入: False (期望 False)
RESULT: PASS
```
stderr 同时打印 `知识层嵌入失败，降级无向量召回: HTTP Error 402: Payment Required`（×3），**证明向量路径确实被降级、走的是关键词路径**。**与「RESULT: PASS」「E2E 标记进入 system prompt」声明一致。**

### 3. 召回指标复核（团队 lead 声称 hits=3 / score=0.6）
实容器内直接调用 `_lexical_retrieve`，以 E2E 风格事实 + 查询 `关税政策怎么样`：
```
query='关税政策怎么样'  -> returned ids: [1] score: 0.6
tokens of query: ['关税', '税政', '政策', '策怎', '么样']
manual hits: 3 score: 0.6 ntok: 5
```
→ 命中 3（关-税、税-政、政-策 三个 bigram 均落在 title「跨境电商关税政策结论」/content 中），`score=3/5=0.6`。**与声称数值一致。**（`怎么` 在 `_STOP` 中被过滤，故 ntok=5 而非 6。）

### 4. 对抗查询（主动证伪注入担忧）
```
tokenize('100% off')     -> ['100', 'off']        # % 被丢弃，永不成 token
tokenize('foo_bar baz')  -> ['foo_bar', 'baz']     # _ 可成 token
tokenize('?!。，')        -> []                     # 纯标点 -> 空
tokenize('税')           -> ['税']                  # 单 CJK 字 -> 1 token
PARAMS: ('%gdp%', '%跨境%', '%境电%', '%电商%', '%关税%', '%税政%', '%政策%')
numbering/params consistent: True refs=[1..7]       # $n 与 params 顺序一致
returned ids (expect only [1]): [1]                  # _ 通配未误召 fooXbar
```

---

## 二、发现（Findings）

### F1 · SQL 注入 / 参数化 —— **安全，担忧不成立**（已证伪）
- 位置：`tools/kb_store.py:148-157`
- `$n` 编号：`idx = len(params)+1` 在 append 前计算，clauses 用 `$1..$n`，`params` 列表按相同顺序 append，`conn.fetch(sql, *params)` 展开。实测 7-token 查询 `$1..$7` 与 7 个 `%...%` 参数一一对应、无错序。
- 用户原始 query **绝不进入 SQL 字符串**，只作为绑定参数值。无拼接、无 f-string 注入面。
- **结论：无注入漏洞。**

### F2 · `%` 通配符注入 —— **不可能发生**（已证伪）
- 位置：`tools/kb_store.py:99` `_WORD = re.compile(r"[0-9a-zA-Z_]+")`
- token 仅来源于拉丁词（字母数字+下划线）与 CJK bigram（`tools/kb_store.py:98` `_CJK_RUN`）。两者都不可能包含 `%`。实测 `tokenize("100% off") -> ['100','off']`，`%` 被正则直接丢弃。
- 因此 `params.append(f"%{t}%")` 中的 `t` 永不含 `%`，ILIKE 模式串里的 `%` 全部来自代码固定拼接，**不存在用户可控的 `%` 注入**。
- **结论：无 `%` 注入风险。**

### F3 · `_` 下划线通配符 —— **MINOR / 无害（被二次打分纠正）**
- 位置：`tools/kb_store.py:99`（`_WORD` 含 `_`）、`:150`（`%{t}%`）、`:161`（Python 字面 `in` 计数）
- 风险点：`foo_bar` 这类含 `_` 的 token 生成 `%foo_bar%`，在 SQL 中 `_` 是 ILIKE 单字符通配，理论上会把 `fooXbar` 等也选入候选。
- **但** SQL 只是取候选集，最终排序/过滤在 Python 用字面子串 `t in hay`（`tools/kb_store.py:161`）完成。`_` 在 Python `in` 中是字面字符，故 `fooXbar` 在重算时 `hit=0` 被 `if hit:` 过滤掉。实测 `foo_bar` 查询只返回含字面 `foo_bar` 的 id=1，未误召 `fooXbar`。
- 残留代价：候选池可能略增大（仍受 `LIMIT 200` 约束），对结果正确性与安全性**无影响**。
- **建议（可选，非必须）**：若追求严谨，可在 `_tokenize` 产出的 token 上对 `_`/`%` 做 `ESCAPE` 转义，或在 `_WORD` 中去掉 `_`。当前实现因二次字面打分已自纠正，不阻断。

### F4 · 空 / 纯标点 / 空白查询 —— **安全**（已证伪）
- 位置：`tools/kb_store.py:143-145`（`if not toks: return []`）
- 实测 `tokenize("?!。，") -> []`，`_lexical_retrieve` 直接返回 `[]`，不触 SQL、不抛错。无崩溃面。

### F5 · 单 CJK 字符查询 —— **NOTE**
- 位置：`tools/kb_store.py:121-122`（len<=2 的 run 原样成 token）
- `tokenize("税") -> ['税']`，会生成 `%税%`，可能匹配大量含「税」的行；但经 Python 按命中数排序 + `k` 截断，只会稀释排序、不致误召无关项进 top-k。当前规模无碍。

### F6 · 排序语义与正确性 —— **正确**
- 位置：`tools/kb_store.py:164`（稳定排序 `key=lambda x:-x[0]`），SQL `ORDER BY created_at DESC`（`tools/kb_store.py:155`）兜底 tie-break。
- 无关行不可能排在相关行之上：score 严格按命中 token 数降序。无 tie 导致乱序的问题。

### F7 · `LIMIT 200` 截断饿死 —— **NOTE（极小概率）**
- 位置：`tools/kb_store.py:155`
- 若某 token 极常见（如高频 bigram）且 `kb.entries` 超过 200 行命中该 token，SQL 仅回 `created_at DESC` 前 200 行，可能把**较旧但更相关**（命中更多 token）的条目截断掉，使其无缘 top-k。
- 当前知识库规模（KB 量级小）不会出现；属可观测的边界，建议未来加注释或改为先按 token 数预筛。

### F8 · 回归风险 —— **可控**
- 向量路径仍优先：`kb_retrieve` 先 `embed([query])`（`tools/kb_store.py:189`），仅当 `vecs` 真值且取到行才返回向量结果；向量列全 NULL / embed 失败 → 落到 `_lexical_retrieve`（`tools/kb_store.py:200-203`）。降级链与注释一致。
- `_lexical_retrieve` 位于 `kb_retrieve` 的 `try/except`（`tools/kb_store.py:183-208`）内，即使其抛异常也被捕获返回 `[]`，**不会穿透**调用方。
- 契约检查：调用方 `chat_agent.py:363/365`、`orchestrator.py:2398/2438`、`server/api.py:1276`、`tools/chat_learning.py:195` 均沿用 `kb_retrieve(query,k)` / `kb_write(entries)` 旧签名；`embed(texts)` 签名未变（`EMBEDDING_DIMENSIONS` 仅为模块内 env 读取）。**无契约破坏。**

### F9 · 性能 —— **当前规模可接受（NOTE）**
- `kb.entries` 上 `content ILIKE ... OR title ILIKE ...` 全表扫描，最多 24 个 OR 子句（`_MAX_TOKENS=24`）。现无 GIN/trigram 索引支撑 ILIKE。
- 当前数据量下延迟可忽略；若 KB 增长，建议加 `pg_trgm` GIN 索引（`content` / `title`）以加速 ILIKE。非阻断。

### F10 · 「跨会话记忆」表述夸大 —— **MAJOR（诚实性标注，非代码 defect）**
- 本修复＝**词面/关键词召回**（bigram + 拉丁词 ILIKE），**不是语义记忆**。嵌入端点仍 `HTTP 402`（实测 stderr 多次打印），`embedding` 列全 NULL，语义召回完全不可用。
- 因此：仅当新查询与已存事实**共享 token**（bigram / 拉丁词）时才能召回；paraphrase 该写法、共享 0 token 的提问**仍会失忆**。例如存「关税下调 3%」、问「进口税费有什么变化」→ bigram 无重叠 → 不召回。
- 团队 lead 自己在注释/脚本中已承认「语义召回不可用」，但对外称「agent 有跨会话记忆」仍属**略夸**：它恢复的是「同词面可回忆」，而非「理解语义后可回忆」。建议交付话术改为「关键词兜底召回（语义召回待充值）」，并尽快修复嵌入账单/换可用 embedding 模型以获得真正跨会话语义记忆。

---

## 三、判定汇总

| 维度 | 结果 |
|---|---|
| SQL 注入 / 参数化 | 安全（F1/F2 证伪） |
| 边界：空/标点/单字 | 安全（F4/F5） |
| `_` 通配 | 无害，二次打分纠正（F3 MINOR） |
| 排序正确性 | 正确（F6） |
| 回归风险 | 可控（F8） |
| 性能 | 可接受，建议 trigram 索引（F9 NOTE） |
| 单测 / E2E | 实测 6/6 + PASS |
| 召回指标声称 | 复核一致（hits=3, score=0.6） |
| 语义诚实性 | 夸大，需澄清（F10 MAJOR 标注） |

**Severity 计数**：BLOCKER 0 · MAJOR 0（代码级）/ 1（诚实性标注 F10） · MINOR 1（F3） · NOTE 4（F5/F7/F9/F10）

---

<!-- reviewed-by: independent-subagent (gate-③) -->
