"""一次性数据修复：把 `chat_sessions.owner_id IS NULL` 的存量会话归位到系统主账号。

## 为什么必须修

多租户改造时给 `chat_sessions` 加了 `owner_id`，但**存量行没有被回填**。
而 `/chat` 的归属校验写的是宽松式：

    if aid and s.owner_id and s.owner_id != aid:   # owner_id 为 NULL → 短路 → 放行

于是 `owner_id IS NULL` 的会话**任何已登录账号都能继续对话**——
它的历史会被读出来喂给模型，等于把别人（或改造前主账号）的聊天内容泄漏给任意租户。
注意这只是**数据 + 一处宽松判断**的组合问题；`GET /chat/sessions/{id}`（`_owned_session`）
用的是严格式 `s.owner_id != aid`，反而不漏 —— 两处不一致本身就是缺陷。

本脚本负责「数据」那一半：把 NULL 归位到主账号（改造前唯一的用户），
使严格校验生效后**原主人依然能访问**，而不是被锁在门外。
代码那一半见 `server/api.py` 的 `/chat` 分支（已改为与 `_owned_session` 一致的严格式）。

## 用法

    python scripts/backfill_null_session_owner.py --dry-run   # 先看
    python scripts/backfill_null_session_owner.py             # 再改

幂等：重复执行第二次会报「0 行」，不会重复改。
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app")

from sqlalchemy import select, update  # noqa: E402

from server.database import get_sync_session  # noqa: E402
from server.models import ChatSession  # noqa: E402


def _resolve_main_account():
    """复用 purge 脚本的判定：is_system_main 优先，其次 role=main。"""
    from scripts.purge_inherited_gateway import _resolve_main_account as _r

    return _r()


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv

    main_id, main_name, err = _resolve_main_account()
    if err or not main_id:
        print(f"❌ 无法确定系统主账号：{err}")
        return 1
    print(f"系统主账号：{main_name} ({main_id})")

    with get_sync_session() as session:
        rows = session.execute(
            select(ChatSession.id, ChatSession.topic, ChatSession.created_at)
            .where(ChatSession.owner_id.is_(None))
            .order_by(ChatSession.created_at)
        ).all()

        if not rows:
            print("✅ 没有 owner_id IS NULL 的会话（已归位 / 幂等复跑）")
            return 0

        print(f"发现 {len(rows)} 个无归属会话：")
        for sid, topic, created in rows:
            print(f"  - {sid}  {created}  topic={(topic or '')[:40]!r} → 归位到 {main_name}")

        if dry:
            print("\n[DRY-RUN] 未改动任何数据。去掉 --dry-run 即执行。")
            return 0

        res = session.execute(
            update(ChatSession)
            .where(ChatSession.owner_id.is_(None))
            .values(owner_id=main_id)
        )
        session.commit()
        print(f"\n✅ 已归位 {res.rowcount} 个会话 → {main_name}")

    # 复核：改完必须为 0，否则等于没修
    with get_sync_session() as session:
        left = session.execute(
            select(ChatSession.id).where(ChatSession.owner_id.is_(None))
        ).all()
    print(f"复核：仍有 {len(left)} 个无归属会话（应为 0）")
    return 0 if not left else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
