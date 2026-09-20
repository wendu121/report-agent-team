"""探针：逐模型查「有没有额度 / 能不能出 JSON」（M13 根因追查）。

背景：子账号三道闸 100% 降级放行。目视验收后追根因发现——闸/评分模型
`custom:discovery:deepseek-v4-flash-vision` 返回 **429 quota exceeded**：
不是"模型出不了 JSON"，而是"模型没额度了"。于是引擎把 LLMError 当成
「审核 LLM 不可用」→ 降级放行。

本脚本列出所有角色/闸的模型，并对**去重后**的每个模型做一次**极小**调用
（约 20 token），报告：OK / 429 配额耗尽 / 其他错误 / 能否解析 JSON。
用于决定「闸该换成哪个模型」。

用法（容器内）：docker compose exec -T api python /app/scripts/probe_model_quota.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import tenancy  # noqa: E402

MAIN_ACCOUNT = "9910ebbc-112f-4ef2-ad7b-ed2582999382"   # wendy（主账号）
SUB_ACCOUNT = "af28b675-e0b8-46ac-96e7-9e4b3e3f485a"    # wendy1（子账号）
ACCOUNT = sys.argv[1] if len(sys.argv) > 1 else MAIN_ACCOUNT
tenancy.set_current_account(ACCOUNT)
print(f"### 账号: {ACCOUNT}（{'主账号 wendy' if ACCOUNT == MAIN_ACCOUNT else '子账号 wendy1' if ACCOUNT == SUB_ACCOUNT else '?'}）")

from orchestrator import (  # noqa: E402
    NewApiLLMClient,
    load_model_interfaces_and_endpoints,
    load_model_mapping,
)

endpoints, interfaces, _, default_endpoint_id = load_model_interfaces_and_endpoints()
llm = NewApiLLMClient(
    endpoints=endpoints, interfaces=interfaces, default_endpoint_id=default_endpoint_id
)

mm = load_model_mapping()
print("=== 角色 / 闸 → 模型 ===")
role_models = {}
for role, cfg in (mm.get("roles") or {}).items():
    role_models[f"role:{role}"] = (cfg.get("model"), cfg.get("base"))
gate_models = {}
for gname, cfg in (mm.get("gates") or {}).items():
    gate_models[f"gate:{gname}"] = (cfg.get("model"), cfg.get("base"),
                                    cfg.get("reviews"))
all_map = dict(role_models)
all_map.update(gate_models)
all_map["eval"] = ((mm.get("eval") or {}).get("model"),
                   (mm.get("eval") or {}).get("base"), "—")
for k, v in all_map.items():
    print(f"  {k:22s} -> {v[0]:<46s} base={v[1]}")

# 去重后逐个探活（极小调用，省额度）
seen = {}
for k, v in all_map.items():
    seen.setdefault(v[0], []).append(k)

SYS = "你是一个只输出 JSON 的助手。"
USER = '只输出：{"ok":true}'

print("\n=== 逐个模型探活（极小调用）===")
for model, users in seen.items():
    if not model:
        continue
    verdict = ""
    try:
        raw = llm.complete(model, SYS, USER, role="Probe")
        text = (raw or "").strip()
        try:
            json.loads(text)
            how = "直接 json.loads ✅"
        except Exception:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    json.loads(m.group(0))
                    how = "抽取后解析 ⚠️"
                except Exception:
                    how = "仍不可解析 ❌"
            else:
                how = "无 JSON ❌"
        verdict = f"OK（{how}） 原文={text[:60]!r}"
    except Exception as e:
        s = str(e)
        if "quota" in s.lower() or "429" in s:
            verdict = "❌ 429 配额耗尽"
        else:
            verdict = f"❌ {type(e).__name__}: {s[:120]}"
    print(f"\n  [{model}]")
    print(f"    用途: {', '.join(users)}")
    print(f"    结果: {verdict}")
