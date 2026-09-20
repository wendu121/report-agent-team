"""探针：探活「候选模型名」，不改任何配置（M13 主账号 inspect 配套）。

与 probe_model_quota.py 的区别：那个遍历**当前配置**里的模型；这个探**任意指定**
的模型名，用于回答「闸坏了该换成哪个」——先探再改，避免盲改产生审计脏记录。

用法（容器内）：
  docker compose exec -T api python /app/scripts/probe_candidate_models.py [account_id] [model1 model2 ...]
不传模型名时探默认候选集。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import tenancy  # noqa: E402

MAIN_ACCOUNT = "9910ebbc-112f-4ef2-ad7b-ed2582999382"   # wendy（主账号）
SUB_ACCOUNT = "af28b675-e0b8-46ac-96e7-9e4b3e3f485a"    # wendy1（子账号）

argv = sys.argv[1:]
account = MAIN_ACCOUNT
if argv and not argv[0].startswith("-") and len(argv[0]) > 30:
    account = argv.pop(0)
candidates = argv or [
    "LongCat-2.0",
    "Intern-S2-Preview",
    "Intern-S2-Preview-397B",
    "llama-3.3-70b",
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
]

tenancy.set_current_account(account)
print(f"### 账号: {account}")

from orchestrator import (  # noqa: E402
    NewApiLLMClient,
    load_model_interfaces_and_endpoints,
)

endpoints, interfaces, _, default_endpoint_id = load_model_interfaces_and_endpoints()
llm = NewApiLLMClient(
    endpoints=endpoints, interfaces=interfaces, default_endpoint_id=default_endpoint_id
)

# 先看清「接口 id → 端点」的解析面，判断候选名是否可命中
print("\n=== 已注册接口 id（前 60）===")
if isinstance(interfaces, dict):
    iids = sorted(interfaces.keys())
else:
    iids = []
    for it in interfaces or []:
        iid = it.get("id") if isinstance(it, dict) else getattr(it, "id", None)
        if iid:
            iids.append(iid)
    iids.sort()
for i in iids[:60]:
    print(f"  - {i}")
print(f"  ...共 {len(iids)} 个；default_endpoint_id={default_endpoint_id}")

SYS = "你是一个只输出 JSON 的助手。"
USER = '只输出：{"ok":true}'

print("\n=== 候选模型探活（极小调用，约 20 token）===")
for model in candidates:
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
        low = s.lower()
        if "quota" in low or "429" in low:
            verdict = "❌ 429/配额耗尽"
        elif "400" in s:
            verdict = f"❌ 400（多半是配额/参数）: {s[:120]}"
        else:
            verdict = f"❌ {type(e).__name__}: {s[:120]}"
    print(f"\n  [{model}]")
    print(f"    结果: {verdict}")
