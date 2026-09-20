#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""端到端验证：子账号模型/密钥隔离（DESIGN_subaccount_model_isolation.md §4）。

在容器内运行（`PYTHONPATH=/app`），对**真实数据库 + 真实租户目录**做断言，不 mock。

判定项（与 DESIGN §4 对齐）：
  A1  新建子账号的 config/ 内不出现主账号网关痕迹（new-api / NEWAPI_API_KEY / LongCat-2.0）
  A2  子账号上下文下 load_model_interfaces_and_endpoints() → endpoints=[] 且 default_endpoint_id=None
      **即使容器环境里 NEWAPI_API_KEY 有值**（核心断言：不静默回落主账号网关）
  A2b 引擎子进程路径（RAT_ACCOUNT_ID 环境变量 + bind_account）同样得到空端点
  A3  该配置下发起 LLM 调用 → 抛 LLMError 且文案是可执行的配置指引（不发任何 HTTP）
  A4  子账号自加 Provider 后 → 端点=自己的 base_url/key，模型解析命中自己的端点
  A5  回归：主账号仍持有 new-api 端点（隔离没有误伤主账号）

用法：
    docker compose exec -T -e PYTHONPATH=/app api python /app/scripts/verify_subaccount_isolation.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, List, Tuple

RESULTS: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"{'✅ PASS' if ok else '❌ FAIL'}  {name}" + (f" — {detail}" if detail else ""))


# --------------------------------------------------------------------- 辅助

def _make_temp_subaccount() -> Tuple[str, str]:
    """建一个临时子账号（直接写库 + 建租户目录 + 走 approve 播种路径），返回 (id, username)。"""
    from server.auth import _approve_impl  # noqa: F401  (仅为确认模块可导入)
    from server.database import get_sync_session
    from server.models import Account
    from server.auth import hash_password
    from server import tenancy
    from datetime import datetime

    uid = str(uuid.uuid4())
    uname = f"_iso_probe_{uid[:8]}"

    from sqlalchemy import select

    with get_sync_session() as session:
        main = (
            session.execute(select(Account).where(Account.is_system_main.is_(True)).limit(1))
        ).scalars().first()
        if main is None:
            raise RuntimeError("找不到系统主账号")
        main_id = main.id
        acc = Account(
            id=uid,
            username=uname,
            password_hash=hash_password("probe-pass-1234"),
            role="sub",
            parent_id=main_id,
            status="active",
            is_system_main=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(acc)
        session.commit()

    # 复刻 approve 的播种路径：seed_from=主账号 → 应被中性模板净化
    tenancy.ensure_account_layout(uid, seed_from=main_id)
    return uid, uname


def _cleanup(uid: str, uname: str) -> None:
    from sqlalchemy import select
    from server.database import get_sync_session
    from server.models import Account
    from server import tenancy
    from server.models import CallRecord

    try:
        with get_sync_session() as session:
            for row in session.execute(select(CallRecord).where(CallRecord.account_id == uid)).scalars().all():
                session.delete(row)
            obj = session.execute(select(Account).where(Account.id == uid)).scalars().first()
            if obj is not None:
                session.delete(obj)
            session.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 清理数据库失败：{exc}")

    root = Path(tenancy.TENANTS_ROOT) / uid
    if root.exists():
        try:
            shutil.rmtree(root)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ 清理租户目录失败：{exc}")


def _strip_yaml_comments(text: str) -> str:
    """去掉 YAML 注释行与行尾注释，只留"生效内容"。

    必要性：中性模板里刻意写了 `# 子账号不共享主账号的 new-api 网关。` 这类**说明性注释**，
    若按原样 grep 会把它当成"仍在引用网关"的假阳性。本断言要问的是
    「有没有**生效的**配置指向主账号网关」，所以必须先剥注释。
    （端点的关键字段 id/base_url/api_key 都在行首，值里也不会含 '#'，剥离足够安全。）
    """
    out = []
    for line in text.splitlines():
        i = line.find("#")
        out.append(line[:i] if i >= 0 else line)
    return "\n".join(out)


def _scan_for_gateway_traces(cfg_dir: Path) -> List[str]:
    """在租户 config/ 里找**生效的**主账号网关痕迹。返回命中列表（空 = 干净）。"""
    needles = ["new-api", "NEWAPI_API_KEY", "NEWAPI_BASE_URL", "LongCat-2.0", "llama-3.3-70b"]
    hits: List[str] = []
    if not cfg_dir.exists():
        return hits
    for f in sorted(cfg_dir.iterdir()):
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        effective = _strip_yaml_comments(text) if f.suffix in (".yaml", ".yml") else text
        for n in needles:
            if n in effective:
                hits.append(f"{f.name}:{n}")
    return hits


# --------------------------------------------------------------- HTTP 层断言
#
# ⚠️ 为什么必须有这一层：A2 是**在进程内手动 set_current_account** 后调加载器，它证明的是
# "有上下文时隔离成立"，**证明不了真实 HTTP 链路会带上上下文**。实测教训——首个版本只有
# A 系列全绿，但真实浏览器里子账号的模型下拉照样列出主账号的 new-api 网关模型：
# 原因是 `/api/v1/models` 当时挂在**无鉴权**的 public_router 上 → 没有 set_current_account
# → 配合 RAT_LEGACY_GLOBAL=1 回落到全局（主账号）配置。故 B 系列直接在 HTTP 上验。
# ---------------------------------------------------------------------------

API_BASE = os.getenv("RAT_SELF_API_BASE", "http://127.0.0.1:8000/api/v1")


def _http_json(method: str, path: str, token: Optional[str] = None,
               body: Optional[dict] = None, timeout: int = 180) -> Tuple[int, Any]:
    import json as _json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(f"{API_BASE}{path}", method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = _json.dumps(body).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            return resp.status, _json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, _json.loads(raw)
        except Exception:  # noqa: BLE001
            return exc.code, {"raw": raw}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def _mint_token(account_id: str) -> str:
    from sqlalchemy import select as _sel
    from server.auth import create_access_token
    from server.database import get_sync_session
    from server.models import Account as _Acc

    with get_sync_session() as session:
        acc = (session.execute(_sel(_Acc).where(_Acc.id == account_id))).scalars().first()
        return create_access_token(acc)


# ------------------------------------------------------------------- 主体

def main() -> int:
    from server import tenancy

    print("=" * 72)
    print("子账号模型/密钥隔离验证")
    print("=" * 72)
    print(f"NEWAPI_API_KEY 是否在容器环境中已设置：{bool(os.getenv('NEWAPI_API_KEY'))}")
    print(f"NEWAPI_BASE_URL = {os.getenv('NEWAPI_BASE_URL')!r}")
    print()

    uid, uname = _make_temp_subaccount()
    print(f"临时子账号：{uname} ({uid})\n")

    try:
        # ---------------- A1：播种后无主账号网关痕迹 ----------------
        cfg_dir = Path(tenancy.TENANTS_ROOT) / uid / "config"
        hits = _scan_for_gateway_traces(cfg_dir)
        check(
            "A1 子账号 config/ 不含主账号网关痕迹",
            not hits,
            f"命中：{hits}" if hits else f"已扫描 {len(list(cfg_dir.iterdir()))} 个文件，0 命中",
        )
        # 顺带确认中性模板确实写进去了（否则"没有痕迹"可能只是因为文件不存在）
        models_yaml = cfg_dir / "models.yaml"
        mapping_yaml = cfg_dir / "model_mapping.yaml"
        check(
            "A1b 中性模板已实际落盘",
            models_yaml.exists() and mapping_yaml.exists(),
            f"models.yaml={models_yaml.exists()} model_mapping.yaml={mapping_yaml.exists()}",
        )

        # ---------------- A2：子账号上下文下拿不到任何端点 ----------------
        tenancy.set_current_account(uid)
        from orchestrator import LLMError, NewApiLLMClient, load_model_interfaces_and_endpoints

        endpoints, interfaces, gw, default_eid = load_model_interfaces_and_endpoints()
        check(
            "A2 子账号 endpoints 为空且无默认端点（不回落 NEWAPI_*）",
            endpoints == [] and default_eid is None,
            f"endpoints={len(endpoints)} 个, default_endpoint_id={default_eid!r}",
        )

        # ---------------- A2b：引擎子进程路径（RAT_ACCOUNT_ID）----------------
        # 用子进程复刻引擎的租户绑定方式，确认不是「只在 API 进程里有效」。
        probe = (
            "import os;"
            "from server import tenancy;"
            "tenancy.bind_account(os.environ['RAT_ACCOUNT_ID']);"
            "from orchestrator import load_model_interfaces_and_endpoints as L;"
            "e,i,g,d=L();"
            "print(f'SUBPROC endpoints={len(e)} default={d!r}')"
        )
        env = dict(os.environ)
        env["RAT_ACCOUNT_ID"] = uid
        env["PYTHONPATH"] = env.get("PYTHONPATH", "/app")
        try:
            out = subprocess.run(
                [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60, env=env
            )
            line = (out.stdout or "").strip().splitlines()[-1] if out.stdout.strip() else ""
            ok = "endpoints=0 default=None" in line
            check("A2b 引擎子进程路径同样得到空端点", ok, line or f"stderr={out.stderr[-300:]}")
        except Exception as exc:  # noqa: BLE001
            check("A2b 引擎子进程路径同样得到空端点", False, f"{type(exc).__name__}: {exc}")

        # ---------------- A3：错误文案可执行，且不发 HTTP ----------------
        llm = NewApiLLMClient(
            endpoints=endpoints, interfaces=interfaces, default_endpoint_id=default_eid,
            max_retries=0, timeout=10,
        )
        msg = ""
        raised = False
        try:
            llm.complete("LongCat-2.0", system="x", user="你好")
        except LLMError as exc:
            raised = True
            msg = str(exc)
        except Exception as exc:  # noqa: BLE001
            msg = f"非 LLMError：{type(exc).__name__}: {exc}"
        check(
            "A3 未配置时抛 LLMError 且文案含可执行指引",
            raised and "自定义 API" in msg and "Provider" in msg,
            msg[:160],
        )

        # ---------------- A4：自加 Provider 后路由到自己的端点 ----------------
        cfg = cfg_dir / "custom_providers.yaml"
        cfg.write_text(
            "providers:\n"
            "- id: probe-provider\n"
            "  name: Probe Provider\n"
            "  base_url: https://probe.example.invalid/v1\n"
            "  model_ids:\n"
            "  - probe-model\n"
            "  enabled: true\n",
            encoding="utf-8",
        )
        sec_dir = Path(tenancy.TENANTS_ROOT) / uid / ".secrets"
        sec_dir.mkdir(parents=True, exist_ok=True)
        (sec_dir / "custom_providers.json").write_text(
            '{"probe-provider": "sk-probe-own-key-1234"}', encoding="utf-8"
        )
        # 让映射指向自己的模型，模拟"用户自己配置模型"
        mapping_yaml.write_text(
            mapping_yaml.read_text(encoding="utf-8").replace('tool_model: ""', 'tool_model: probe-model'),
            encoding="utf-8",
        )

        endpoints2, interfaces2, _gw2, default_eid2 = load_model_interfaces_and_endpoints()
        ep = next((e for e in endpoints2 if e["id"] == "custom:probe-provider"), None)
        check(
            "A4a 自加 Provider 后端点=自己的 base_url/key",
            ep is not None
            and ep.get("base_url") == "https://probe.example.invalid/v1"
            and ep.get("api_key") == "sk-probe-own-key-1234",
            f"endpoint={ {k: ep.get(k) for k in ('id', 'base_url', 'api_key')} if ep else None }",
        )

        llm2 = NewApiLLMClient(
            endpoints=endpoints2, interfaces=interfaces2, default_endpoint_id=default_eid2,
            max_retries=0, timeout=10,
        )
        eid, real, cfgp = llm2._resolve("probe-model")
        check(
            "A4b 模型解析命中子账号自己的端点",
            eid == "custom:probe-provider" and real == "probe-model",
            f"endpoint_id={eid!r} real_model={real!r} base_url={cfgp.get('base_url')!r}",
        )

        # ---------------- A5：主账号回归 ----------------
        from sqlalchemy import select as _select
        from server.database import get_sync_session
        from server.models import Account as _Account

        with get_sync_session() as session:
            main = (
                session.execute(_select(_Account).where(_Account.is_system_main.is_(True)).limit(1))
            ).scalars().first()
            main_id = main.id
            main_user = main.username

        tenancy.set_current_account(main_id)
        me, mi, _mg, md = load_model_interfaces_and_endpoints()
        check(
            "A5 回归：主账号仍持有 new-api 端点",
            any(e["id"] == "new-api" for e in me) and md == "new-api",
            f"主账号={main_user} endpoints={[e['id'] for e in me]} default={md!r}",
        )

        # ---------------- B 系列前置：清掉 A4 写入的探针 Provider ----------------
        # ⚠️ 血泪：首跑 B4 返回 200 且 reply="抱歉，脑子打结了：… Connection error."，
        # 一度被误判为"产品把配置缺失吞掉了"。真因是 **A4 往同一个账号写了探针 provider**，
        # 于是 B4 面对的是"已配置（但地址是假域名）"的账号 —— 测试前提被自己破坏了。
        # 教训与 MEMORY 坑 6 同源：用例状态没归零，结论就不可信。
        cfg.write_text("providers: []\n", encoding="utf-8")
        sec_json = sec_dir / "custom_providers.json"
        if sec_json.exists():
            sec_json.unlink()
        mapping_yaml.write_text(
            mapping_yaml.read_text(encoding="utf-8").replace("tool_model: probe-model", 'tool_model: ""'),
            encoding="utf-8",
        )
        # ⚠️ 必须重新绑回子账号上下文：A5 刚把上下文切到**主账号**，不绑回就会读到主账号
        # 的 models.yaml（2 个端点 + default new-api），把 B0 误判成"重置失败"。
        tenancy.set_current_account(uid)
        _re_eps, _re_ifs, _re_gw, _re_dflt = load_model_interfaces_and_endpoints()
        check(
            "B0 探针 Provider 已清除（B 系列前提：账号回到「未配置」状态）",
            _re_eps == [] and _re_dflt is None,
            f"重置后 endpoints={len(_re_eps)} default={_re_dflt!r}",
        )

        # ================= B 系列：真实 HTTP 链路（鉴权 → 租户上下文 → 配置）=================
        print("\n--- B 系列：真实 HTTP 链路 ---")
        sub_token = _mint_token(uid)
        main_token = _mint_token(main_id)
        _GW_MARKERS = ("new-api", "gateway", "auto-chat", "LongCat-2.0")

        def _leaks(items: Any) -> List[str]:
            out: List[str] = []
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                blob = f"{it.get('id')}|{it.get('source')}|{it.get('endpoint_id')}"
                if any(mk in blob for mk in _GW_MARKERS):
                    out.append(blob)
            return out

        # B1：子账号经 HTTP 拿到的是**自己的**模型清单（应为空/仅占位项）
        code, data = _http_json("GET", "/models", token=sub_token)
        leaks = _leaks(data.get("items"))
        check(
            "B1 HTTP：子账号 /models 不含主账号网关模型",
            code == 200 and not leaks,
            f"HTTP {code}，items={len(data.get('items') or [])}，泄漏项={leaks}",
        )

        # B2：主账号经 HTTP 仍拿到网关模型（回归）
        code_m, data_m = _http_json("GET", "/models", token=main_token)
        check(
            "B2 回归 HTTP：主账号 /models 仍含 new-api 网关模型",
            code_m == 200 and any(
                (it or {}).get("endpoint_id") == "new-api" for it in (data_m.get("items") or [])
            ),
            f"HTTP {code_m}，items={len(data_m.get('items') or [])}",
        )

        # B3：不带 token → 必须 401（旧行为是 200 + 泄漏主账号配置）
        code_a, _data_a = _http_json("GET", "/models", token=None)
        check(
            "B3 无 token 调 /models 返回 401（不再泄漏）",
            code_a == 401,
            f"HTTP {code_a}",
        )

        # B4：子账号真发一条对话 → 必须得到"去配置 API"的可执行报错，而不是真调用主账号网关
        code_c, data_c = _http_json(
            "POST", "/chat", token=sub_token, body={"message": "你好", "history": []}
        )
        detail = data_c.get("detail") if isinstance(data_c, dict) else None
        msg = ""
        if isinstance(detail, dict):
            msg = str(detail.get("message") or "")
        elif isinstance(detail, str):
            msg = detail
        check(
            "B4 HTTP：未配置模型的子账号对话被拦住且文案可执行",
            code_c >= 400 and ("自定义 API" in msg or "Provider" in msg),
            f"HTTP {code_c}，detail={msg[:120]!r}，reply={(data_c.get('reply') or '')[:200]!r}，intent={data_c.get('intent')!r}",
        )

    finally:
        _cleanup(uid, uname)
        print(f"\n临时子账号 {uname} 与其租户目录已清理")

    print()
    print("=" * 72)
    passed = sum(1 for _n, ok, _d in RESULTS if ok)
    total = len(RESULTS)
    for n, ok, d in RESULTS:
        print(f"  {'✅' if ok else '❌'} {n}")
    print(f"\n结果：{passed}/{total} 通过")
    print("=" * 72)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
