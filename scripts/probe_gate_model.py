"""探针：子账号「闸模型」到底能不能出 JSON（M13 目视验收后追查根因）。

目的：判断三道闸 100% 降级是「模型出不了 JSON」还是「模型压根不可用」——
两者修法完全不同：前者是 prompt/解析问题，后者是配置问题（换模型即可恢复真审）。

用法（容器内）：docker compose exec -T api python /app/scripts/probe_gate_model.py
"""
import json
import os
import sys

# 容器里 `python /app/scripts/x.py` 只会把 /app/scripts 入 sys.path，需显式补 /app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import tenancy  # noqa: E402

ACCOUNT = "af28b675-e0b8-46ac-96e7-9e4b3e3f485a"
tenancy.set_current_account(ACCOUNT)

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
gate_models = {}
for gname, g in (mm.get("gates") or {}).items():
    gate_models[gname] = g.get("model")
eval_model = (mm.get("eval") or {}).get("model")
print("=== 当前配置的模型 ===")
for k, v in gate_models.items():
    print(f"  {k:6s} -> {v}")
print(f"  {'eval':6s} -> {eval_model}")
print()

# 与引擎闸节点同形态的 prompt：明确要求只输出 JSON
SYS = "你是研报质量审核闸。只输出一个 JSON 对象，不要任何解释文字。"
USER = (
    "请审核以下产物并给出结论，输出格式严格为：\n"
    '{"decision":"advance|rework|escalate","reason":"简短理由","eval_score":0.9,'
    '"problem_points":["问题1"]}\n\n'
    "待审核内容：本次检索覆盖用户全部子问题，来源可信度均为 high，结论均带有 source_ids。"
)

targets = []
seen = set()
for gname, m in list(gate_models.items()) + [("eval", eval_model)]:
    if m and m not in seen:
        seen.add(m)
        targets.append((gname, m))

for label, model in targets:
    print(f"=== 探测 {label} / {model} ===")
    try:
        raw = llm.complete(model, SYS, USER, role="Gate")
    except Exception as e:
        print(f"  ❌ 调用抛异常：{type(e).__name__}: {str(e)[:200]}")
        print()
        continue
    text = (raw or "").strip()
    print(f"  🔤 原始返回（前 400 字）：{text[:400]!r}")
    # 试着解析，看引擎为什么判它"不可解析"
    try:
        parsed = json.loads(text)
        print(f"  ✅ 可直接 json.loads：{json.dumps(parsed, ensure_ascii=False)[:200]}")
    except Exception:
        import re
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                parsed = json.loads(m.group(0))
                print(f"  ⚠️ 需抽取花括号后才能解析：{json.dumps(parsed, ensure_ascii=False)[:200]}")
            except Exception as e2:
                print(f"  ❌ 抽取花括号后仍失败：{e2}")
        else:
            print("  ❌ 返回里根本没有 JSON 花括号 —— 引擎判定「不可解析」的直接原因")
    print()
