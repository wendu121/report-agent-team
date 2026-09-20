#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DESIGN_source_honesty §5 验收：检索源诚实降级的可复现证据脚本。

为什么需要它：2026-09-19 发现「缺密钥的 api_key 源被静默替换成占位数据」——
前端显示 8 条假「来源」、日志把占位记成「命中 8 条」、真源结果被挤出展示位。
本脚本把「不可用就明说不可用」钉成断言，防止将来有人为了「不崩」把静默兜底加回来。

运行（容器内）：
  docker compose -f docker-compose.yml exec -T -e PYTHONPATH=/app \\
      api python -u /app/scripts/verify_source_honesty.py

断言 S1-S7 全部为**确定性离线**用例（不依赖网络/LLM），任何 FAIL → exit 1。
"""
from __future__ import annotations

import io
import sys
import contextlib

sys.path.insert(0, "/app")

PASS, FAIL = "PASS", "FAIL"
_results: list[tuple[str, bool, str]] = []


def check(tag: str, ok: bool, detail: str = "") -> None:
    _results.append((tag, bool(ok), detail))
    print(f"[{PASS if ok else FAIL}] {tag}" + (f"  —— {detail}" if detail else ""))


def _spec(pid: str, provider: str, auth: str = "api_key", name: str = "") -> dict:
    return {"id": pid, "name": name or pid, "provider": provider,
            "auth_type": auth, "enabled": True, "category": "综合检索"}


def main() -> int:
    from tools.data_sources import build_search_tool, MockProvider
    import tools as tools_pkg

    # ---- S1：缺密钥的 api_key 源不注册、不产出任何结果（绝不伪造） ----
    tool = build_search_tool([_spec("tavily", "tavily", name="Tavily 搜索")], {})
    n_prov = len(tool.providers)
    res, status = tool.search_many(["2026 中国外卖 市场规模"], agent="verify")
    check("S1 缺密钥源不注册且零结果",
          n_prov == 0 and len(res) == 0,
          f"providers={n_prov} results={len(res)}")

    # ---- S2：degraded 精确报告 id / reason / fix ----
    d = tool.degraded[0] if tool.degraded else {}
    ok2 = (len(tool.degraded) == 1 and d.get("id") == "tavily"
           and d.get("reason") == "missing_key" and isinstance(d.get("fix"), str)
           and len(d.get("fix") or "") > 0)
    check("S2 degraded 含 id/reason/fix", ok2, f"degraded={tool.degraded}")

    # ---- S3a：显式 mock 才 using_mock_search=True，且结果带 is_mock ----
    tm = build_search_tool([_spec("demo_mock", "mock", auth="none", name="占位源")], {})
    rm, _ = tm.search_many(["q"], agent="verify")
    check("S3a 显式 mock → using_mock_search=True 且结果带 is_mock",
          tm.using_mock_search is True and len(rm) > 0 and all(r.get("is_mock") for r in rm),
          f"flag={tm.using_mock_search} rows={len(rm)} is_mock={[bool(r.get('is_mock')) for r in rm]}")

    # ---- S3b：真 keyless 源 → using_mock_search=False，结果不带 is_mock ----
    tk = build_search_tool([_spec("wikipedia", "wikipedia", auth="none", name="Wikipedia")], {})
    real_provs = [p for _s, p in tk.providers if not isinstance(p, MockProvider)]
    check("S3b keyless 真源 → using_mock_search=False",
          tk.using_mock_search is False and len(real_provs) == 1,
          f"flag={tk.using_mock_search} real_providers={len(real_provs)}")

    # ---- S5：build_tools 逐源打印 [WARN]，含源名与「密钥」字样，并透传 degraded ----
    #      注意：`_config_dir()` 走**租户命名空间**（tenants/<账号>/config），不认临时目录，
    #      所以这里用「模块级注入」替换 load_data_sources / load_secrets，不改真实租户配置。
    import tools.data_sources as ds
    _orig_load_specs, _orig_load_secrets = ds.load_data_sources, ds.load_secrets
    try:
        ds.load_data_sources = lambda: [_spec("tavily", "tavily", name="Tavily 搜索"),
                                        _spec("wikipedia", "wikipedia", auth="none",
                                              name="Wikipedia")]
        ds.load_secrets = lambda: {}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bundle = tools_pkg.build_tools(config_path="/tmp/nonexistent_tools.yaml")
        printed = buf.getvalue()
        warn_lines = [ln for ln in printed.splitlines() if "tavily" in ln.lower()]
        ok5 = ("[WARN]" in printed and "未参与检索" in printed
               and any("密钥" in ln and "修复" in ln for ln in warn_lines))
        check("S5 build_tools 逐源打印缺密钥告警（含修复指引）",
              ok5, "告警行: " + (warn_lines[0].strip()[:130] if warn_lines else "(未找到含 tavily 的行)"))
        check("S5b ToolBundle.degraded 透传",
              [x.get("id") for x in (bundle.degraded or [])] == ["tavily"],
              f"degraded={bundle.degraded}")
    except Exception as e:  # noqa: BLE001
        check("S5 build_tools 逐源打印缺密钥告警（含修复指引）", False,
              f"用例构造异常: {type(e).__name__}: {e}")
        check("S5b ToolBundle.degraded 透传", False, "因 S5 异常未执行")
    finally:
        ds.load_data_sources, ds.load_secrets = _orig_load_specs, _orig_load_secrets

    # ---- S6：using_mock_search 不得由「是否还有别的真源」推导 ----
    mix = build_search_tool([_spec("tavily", "tavily", name="Tavily 搜索"),
                             _spec("wikipedia", "wikipedia", auth="none", name="Wikipedia")], {})
    rmix, _ = mix.search_many(["q"], agent="verify")
    check("S6 有真源时全局降级标志不得被中和",
          [x.get("id") for x in mix.degraded] == ["tavily"]
          and mix.using_mock_search is False
          and all(not r.get("is_mock") for r in rmix),
          f"degraded={[x.get('id') for x in mix.degraded]} flag={mix.using_mock_search} rows={len(rmix)}")

    # ---- S7：status 里必须出现降级条目（ok=False），不静默 ----
    degr_status = [s for s in status if s.get("source") == "tavily"]
    check("S7 search_many status 记录降级源(ok=False)",
          bool(degr_status) and all(s.get("ok") is False for s in degr_status),
          f"{degr_status[:1]}")

    # ---- S8：legacy 回退分支不再硬编码 DS_TAVILY_API_KEY ----
    #      对抗性条件：secrets 里**只有** tavily 的 key，而 provider 是 duckduckgo。
    #      旧代码查 DS_TAVILY_API_KEY → 会误报 connected；新代码查 DS_DUCKDUCKGO_API_KEY → disconnected。
    import tempfile as _tf, shutil as _sh
    from pathlib import Path as _P
    tmp2 = _tf.mkdtemp(prefix="legacy_")
    try:
        import yaml
        d2 = _P(tmp2)
        (d2 / "tools.yaml").write_text(
            yaml.safe_dump({"web_search": {"provider": "duckduckgo"}}), encoding="utf-8")
        _orig_dir = ds._config_dir
        _orig_sec = ds.load_secrets
        ds._config_dir = lambda: d2          # 只有 tools.yaml、无 plugins.yaml → 走 legacy 回退
        ds.load_secrets = lambda: {"DS_TAVILY_API_KEY": "adversarial-dummy"}
        try:
            rows = ds.load_data_sources()
        finally:
            ds._config_dir, ds.load_secrets = _orig_dir, _orig_sec
        row = rows[0] if rows else {}
        check("S8 legacy 回退用通用 DS_<ID>_API_KEY（非硬编码 tavily）",
              row.get("provider") == "duckduckgo" and row.get("status") == "disconnected",
              f"row={ {k: row.get(k) for k in ('id', 'provider', 'status')} }（旧代码此处会误报 connected）")
    except Exception as e:  # noqa: BLE001
        check("S8 legacy 回退用通用 DS_<ID>_API_KEY（非硬编码 tavily）", False,
              f"用例构造异常: {type(e).__name__}: {e}")
    finally:
        _sh.rmtree(tmp2, ignore_errors=True)

    total = len(_results)
    npass = sum(1 for _t, ok, _d in _results if ok)
    print("\n" + "=" * 62)
    print(f"结果：{npass}/{total} PASS")
    print("=" * 62)
    return 0 if npass == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
