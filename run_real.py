# run_real.py · M5 真实网关运行器（经 new-api 网关跑端到端研报）
#
# 设计边界（DESIGN.md v2.5 §9 / M5）：
#   - 仅负责"把编排器接到真实 LLM 网关"，不实现任何编排/角色/闸逻辑；
#   - 网关地址/密钥由环境变量注入，不硬编码（与 model_mapping.yaml 一致）；
#   - 网关不可达时，编排器本身会把首个 LLM 失败转 rework → 超限 escalate，
#     本运行器只负责把结果落盘审计，不伪造"成功"。
#
# 环境变量：
#   NEWAPI_BASE_URL     网关 /v1 地址（默认 http://localhost:3000/v1；请按实际 new-api 端口覆盖）
#   NEWAPI_API_KEY      网关 key（默认 sk-no-key；new-api 通常任意非空 key 即可）
#   NEWAPI_MAX_RETRIES  单次调用失败重试次数（默认 2；演示网关不可达时可设 0 加速）
#   NEWAPI_TIMEOUT      单次请求超时秒数（默认 120；flaky 网关下防止慢/假死上游无限挂起）
#   TAVILY_API_KEY      真实联网检索（可选；缺失则 tools.yaml 降级为 MOCK 占位，不冒充真实检索）
#   REAL_TOPIC / REAL_SCOPE  覆盖默认任务主题/子问题（逗号分隔）
#
# 用法：
#   python run_real.py
#   MODE=real python orchestrator.py        # 等价入口

import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path

from orchestrator import (
    run_report, _summarize, NewApiLLMClient, LLMError, load_model_mapping,
    load_model_interfaces_and_endpoints,
)
from tools import build_tools

BASE = Path(__file__).parent


def _load_mm_models() -> set[str]:
    try:
        mm = load_model_mapping()
        vals = []
        for grp in ("roles", "gates", "eval"):
            for v in (mm.get(grp) or {}).values():
                if isinstance(v, dict) and v.get("model"):
                    vals.append(v["model"])
        return set(vals)
    except Exception:
        return set()


def main():
    base_url = os.getenv("NEWAPI_BASE_URL", "http://localhost:3000/v1")
    api_key = os.getenv("NEWAPI_API_KEY", "sk-no-key")
    max_retries = int(os.getenv("NEWAPI_MAX_RETRIES", "2"))
    timeout = int(os.getenv("NEWAPI_TIMEOUT", "120"))

    print(f"[config] MODE=real  max_retries={max_retries}  timeout={timeout}s")

    # 通用 OpenAI 兼容端点（DESIGN_OPENAI_ENDPOINTS.md）：endpoints/interfaces 从 config/models.yaml 读取
    endpoints, interfaces, _, default_endpoint_id = load_model_interfaces_and_endpoints()
    print(f"[config] 端点 {len(endpoints)} 个，默认端点={default_endpoint_id}")
    client = NewApiLLMClient(
        endpoints=endpoints,
        interfaces=interfaces,
        default_endpoint_id=default_endpoint_id,
        max_retries=max_retries,
        timeout=timeout,
    )

    # 1) 连通性预检（非致命：失败仅告警，运行将走 escalate 验证容错）
    try:
        pf = client.preflight()
        print(f"[preflight] ✅ 网关可达，可用模型 {len(pf['models'])} 个")
        mm_models = _load_mm_models()
        missing = sorted(mm_models - set(pf["models"]))
        if missing:
            print(f"[preflight] ⚠️ model_mapping 中以下模型不在网关可用列表（将依赖网关别名路由）：{missing}")
        else:
            print("[preflight] ✅ model_mapping 全部模型均在网关可用列表中")
    except LLMError as e:
        print(f"[preflight] ⚠️ 网关不可达：{e}")
        print("   → 继续真实运行；首个 LLM 调用将触发 rework→escalate（验证故障容错路径，非失败）。")

    # 2) 工具（Tavily 真实检索需 TAVILY_API_KEY；否则 MOCK 占位并告警）
    bundle = build_tools()
    print(f"[tools] web_search={'MOCK(占位)' if bundle.using_mock_search else 'TAVILY(真实)'}"
          f"  data_proc=on  doc_export=on(export_dir={bundle.doc_export.export_dir})")

    # 3) 任务（可经环境变量覆盖）
    task = {
        "topic": os.getenv("REAL_TOPIC", "2026 年 AI 芯片市场研报"),
        "scope": os.getenv("REAL_SCOPE", "供给,需求,竞争格局,政策与风险").split(","),
        "output_format_spec": "markdown",
        "constraints": ["中文输出", "结论需标注来源"],
    }
    print(f"[task] topic={task['topic']}  scope={task['scope']}")

    # 4) 真实运行
    t0 = time.time()
    final = run_report(task, client, max_rounds=2, tools=bundle)
    dt = time.time() - t0
    print(_summarize(final))
    print(f"[perf] 真实运行耗时 {dt:.1f}s")

    # 5) 审计落盘（无论 done/escalated 都留痕）
    rs = final.get("routing_state", {})
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    audit = BASE / "outputs" / f"real_run_{stamp}.json"
    audit.write_text(json.dumps(final, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"[audit] 完整状态已写入 {audit}")

    if rs.get("status") == "escalated":
        print("\n⚠️ 任务 escalate（非终稿）。可能原因：网关不可达 / 模型名不匹配 / 真实验证未通过。")
        print("   排查：确认 new-api 已启动且 NEWAPI_BASE_URL 正确；或查看 [preflight] 模型列表。")
        sys.exit(2)


if __name__ == "__main__":
    main()
