#!/usr/bin/env python3
"""真实 LLM 接口 e2e 验证：通用 OpenAI 兼容 + 输入 token 预算闸（防 new-api 400）。

验证项：
  1) preflight  —— 连 new-api、列模型（证明 OpenAI 兼容 + 连通）。
  2) 真实 complete —— 短提问跑通（证明 new-api 真实出 token）。
  3) 预算压测 —— 超大 user + 极小 max_input_tokens，证明 fit_input_budget 截断 + input-too-long 自愈，绝不 400 崩。
  4) estimate_tokens 中英文计数 sanity。

用法：
  # 沙箱（new-api 在 127.0.0.1:3000）
  NEWAPI_BASE_URL=http://127.0.0.1:3000 NEWAPI_API_KEY=<key> python3 scripts/verify_llm_openai_e2e.py
  # 宿主机（用 .env 的 NEWAPI_BASE_URL，通常 http://172.29.0.1:3000）
  NEWAPI_API_KEY=<key> python3 scripts/verify_llm_openai_e2e.py
"""
import os
import sys

# 本地 new-api 是 http，绝不走出口代理（沙箱代理只拦 TLS 隧道）
for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "GIT_PROXY_COMMAND"):
    os.environ.pop(k, None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from orchestrator import NewApiLLMClient, estimate_tokens, fit_input_budget  # noqa: E402

KEY = os.getenv("NEWAPI_API_KEY")
if not KEY:
    # 回退：从仓库 .env 读取（.env 已被 gitignore，不会入库，安全）
    envp = os.path.join(REPO, ".env")
    if os.path.exists(envp):
        for line in open(envp, encoding="utf-8", errors="replace"):
            if line.startswith("NEWAPI_API_KEY="):
                KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
if not KEY:
    print("缺少 NEWAPI_API_KEY（请经 env 或仓库 .env 提供）")
    sys.exit(2)
BASE = os.getenv("NEWAPI_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
if not BASE.endswith("/v1"):
    BASE += "/v1"


def build():
    endpoints = [{
        "id": "new-api", "name": "new-api", "base_url": BASE,
        "api_key": KEY, "default": True,
    }]
    interfaces = [
        {"id": "auto-chat", "kind": "universal", "endpoint": "new-api", "model_id": "auto-chat"},
        {"id": "LongCat-2.0", "kind": "concrete", "endpoint": "new-api", "model_id": "LongCat-2.0"},
    ]
    return NewApiLLMClient(endpoints=endpoints, interfaces=interfaces,
                           default_endpoint_id="new-api", max_input_tokens=128000)


def main():
    print(f"new-api base_url = {BASE}")
    c = build()

    print("\n=== [1] preflight（OpenAI 兼容 + 连通）===")
    try:
        pf = c.preflight()
        print("  ok:", pf.get("ok"), "| 模型数:", len(pf.get("models", [])))
    except Exception as e:
        print("  preflight FAILED:", repr(e))

    print("\n=== [2] 真实 complete（auto-chat 短提问）===")
    try:
        out = c.complete("auto-chat", "你是中文助手，只回一句话。", "用一句话介绍 LangGraph。")
        print("  OK 长度", len(out), "->", out[:200].replace("\n", " "))
    except Exception as e:
        print("  complete FAILED:", repr(e), "(若是 429 为网关配额，非代码问题)")

    print("\n=== [3] 预算压测（超大 user，max_input_tokens=8000）===")
    huge = "关于锂电池行业的供需格局与技术路线。" * 4000
    print("  超大 user 估算 token:", estimate_tokens(huge))
    c8 = NewApiLLMClient(
        endpoints=[{"id": "new-api", "name": "new-api", "base_url": BASE, "api_key": KEY, "default": True}],
        interfaces=[{"id": "auto-chat", "kind": "universal", "endpoint": "new-api", "model_id": "auto-chat"}],
        default_endpoint_id="new-api", max_input_tokens=8000)
    sys_b, usr_b = fit_input_budget("请总结下面的文本。", huge, 8000 - 4096)
    print("  截断后 user 估算 token:", estimate_tokens(usr_b), "(应 <= ~3900)")
    try:
        out = c8.complete("auto-chat", "请总结下面的文本。", huge)
        print("  压测 OK 长度", len(out), "->", out[:150].replace("\n", " "))
    except Exception as e:
        print("  压测 FAILED:", repr(e))

    print("\n=== [3b] 空 system 压测（证明 fit_input_budget #1 修复：system 为空仍裁 user）===")
    try:
        out = c8.complete("auto-chat", "", huge)  # system 为空 + 超大 user
        print("  空 system 压测 OK 长度", len(out), "(证明 user 仍被截断，未 400)")
    except Exception as e:
        print("  空 system 压测 FAILED:", repr(e), "(#1 未修会出现 400 循环耗尽)")

    print("\n=== [4] estimate_tokens sanity ===")
    print("  中文 48 字:", estimate_tokens("人工智能" * 12), "(约 48*1.6≈77)")
    print("  英文 20 词:", estimate_tokens("artificial intelligence " * 10), "(约 20*0.25≈5 + 拉丁字符)")
    print("\nDONE")


if __name__ == "__main__":
    main()
