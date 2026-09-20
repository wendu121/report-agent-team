"""真机 e2e：以 wendy1 身份走完整「对话 → 生成研报」链路，验证引擎能真正启动。

背景：修复前 `{TENANTS_ROOT}/<aid>/.engine_state` 不存在 → 引擎启动即
FileNotFoundError → 任务 5ms 内 escalated。本脚本是那个 bug 的回归门。

只看事实，不做断言美化：把每一步的真实返回值打出来。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, "/app")

from sqlalchemy import select  # noqa: E402

from server.auth import create_access_token  # noqa: E402
from server.database import get_sync_session  # noqa: E402
from server.models import Account  # noqa: E402
from server.tenancy import TENANTS_ROOT  # noqa: E402

USERNAME = sys.argv[1] if len(sys.argv) > 1 else "wendy1"
PROMPT = "帮我生成一份关于2026年中国外卖行业的报告"
API = "http://127.0.0.1:8000/api/v1"


def call(path, token, method="GET", body=None, timeout=600):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, method=method, data=data)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, {"raw": raw[:300]}


with get_sync_session() as s:
    acc = s.execute(select(Account).where(Account.username == USERNAME)).scalars().first()
    if acc is None:
        print(f"FAIL 找不到账号 {USERNAME}")
        raise SystemExit(1)
    # 必须在 session 内取属性 / 签发 token：出了 with 上下文 ORM 对象已 detach，
    # 再访问 acc.id 会抛 DetachedInstanceError（本脚本第一版就踩了这个）。
    aid, uname = acc.id, acc.username
    tok = create_access_token(acc)
state_dir = TENANTS_ROOT / aid / ".engine_state"
print(f"账号 {uname} id={aid}")
print(f"租户引擎状态目录: {state_dir}")
print(f"  修复前是否存在: {state_dir.exists()}   （现在是修复后，应已被创建/可被创建）")
print("=" * 70)

# 1) 对话触发研报
print(f"[1] 发对话: {PROMPT!r}")
t0 = time.time()
st, resp = call("/chat", tok, "POST", {"message": PROMPT, "history": []}, timeout=600)
print(f"    HTTP {st}  耗时 {time.time()-t0:.1f}s")
print(f"    intent={resp.get('intent')} reply={str(resp.get('reply'))[:60]!r}")
task_id = resp.get("task_id")
print(f"    task_id={task_id}")
if not task_id:
    print("FAIL 没拿到 task_id，链路没走到研报")
    raise SystemExit(1)

print(f"    session_id={resp.get('session_id')}")
print(f"    user_message_id={resp.get('user_message_id')} assistant_message_id={resp.get('assistant_message_id')}")

print("=" * 70)
# 2) 引擎状态目录 + 输入文件（修复的核心证据）
print(f"[2] 引擎状态目录 {state_dir}")
print(f"    目录存在: {state_dir.exists()}")
if state_dir.exists():
    for f in sorted(state_dir.iterdir()):
        print(f"      - {f.name}  ({f.stat().st_size}B)")
print(f"    input.json 存在: {(state_dir / f'{task_id}_input.json').exists()}")

print("=" * 70)
# 3) 轮询任务状态
# 时长预算：实测同一用例可跑到 ~486s（GateA 可能 rework 到 round=2），原 480s 窗口
# 会赶在引擎收敛前先超时，与「真失败」在输出上无法区分。放宽到 900s，并把
# 「超时未收敛」与「终态但没有报告」分开报告。
print("[3] 轮询任务状态（最多 15 分钟）")
deadline = time.time() + 900
last = None
final = {}
timed_out = True
while time.time() < deadline:
    st, t = call(f"/tasks/{task_id}", tok)
    if st != 200:
        print(f"    [{time.time()-t0:6.1f}s] GET /tasks -> HTTP {st} {str(t)[:100]}")
        time.sleep(5)
        continue
    cur = (t.get("status"), t.get("routing_state", {}).get("round"), t.get("updated_at"))
    if cur != last:
        print(f"    [{time.time()-t0:6.1f}s] status={t.get('status')} round={t.get('routing_state',{}).get('round')}")
        last = cur
    final = t
    if t.get("status") in ("done", "escalated", "failed"):
        timed_out = False
        break
    time.sleep(5)

print("=" * 70)
print(f"[4] 终态: {final.get('status')}")
print(f"    escalate_reason: {str(final.get('escalate_reason'))[:300]}")
md = final.get("report_markdown") or ""
print(f"    report_markdown 长度: {len(md)}")
if md:
    print("    ---- 报告开头 ----")
    print("    " + md[:400].replace("\n", "\n    "))
print(f"    总耗时 {time.time()-t0:.1f}s")

print("=" * 70)
print("[5] 状态目录最终内容")
if state_dir.exists():
    for f in sorted(state_dir.iterdir()):
        print(f"      - {f.name}  ({f.stat().st_size}B)")
print("=" * 70)
ok = final.get("status") == "done" and len(md) > 0
if ok:
    print("结论: ✅ 引擎真的跑通并产出报告")
    sys.exit(0)
# 终态非 done / 无报告 / 超时未收敛 → 非 0 退出。
# 修复前这里只打印 ❌ 却仍 exit 0，导致本脚本作为回归门禁**永远不会变红**，
# 「回归全绿」是假的（F4，两份独立审议共同指出）。
if timed_out:
    print(
        f"结论: ❌ 超时未收敛（{time.time()-t0:.1f}s 内未达 done/escalated/failed，"
        f"最后 status={final.get('status')}）"
    )
else:
    print(f"结论: ❌ 未产出报告（status={final.get('status')}）")
sys.exit(1)
