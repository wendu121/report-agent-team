# tools/chat_learning.py · 对话入口的自主学习 + 经验手册（playbook）
#
# 职责：
# - chat_learn_write：把「工具支撑的实质性回答」沉淀为 kb.entries(kind=chat_fact)，
#   复用 M11-1 持久知识层（pgvector）。失败降级，绝不阻断对话。
# - load_playbook：读 config/chat_playbook.yaml 的「已采纳经验」，注入 ChatAgent system prompt（进化）。
# - propose_lesson / list_proposed / accept_lesson：经验草稿 → 人工采纳门禁
#   （对齐 M11-2 D4 诚实边界：绝不静默自改 prompt/代码，须管理员 accept 才生效）。
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LEGACY_PLAYBOOK_CFG = Path(__file__).resolve().parent.parent / "config" / "chat_playbook.yaml"
PLAYBOOK_CFG = LEGACY_PLAYBOOK_CFG  # 兼容旧引用（读路径请用 _playbook_cfg()）


def _playbook_cfg() -> Path:
    """按当前账号命名空间解析 playbook（各自积累的经验，不跨账号共享）。"""
    try:
        from server import tenancy
        return tenancy.config_path("chat_playbook.yaml")
    except Exception:
        return LEGACY_PLAYBOOK_CFG


def _read_yaml(p: Path) -> dict:
    try:
        import yaml

        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}


def _write_yaml_atomic(p: Path, data: dict) -> None:
    """ruamel round-trip 优先（保注释），失败回落标准 yaml + 原子 rename（对齐 M8-1）。"""
    try:
        import ruamel.yaml

        ry = ruamel.yaml.YAML()
        ry.preserve_quotes = True
        tmp = p.with_suffix(p.suffix + ".tmp")
        ry.dump(data, tmp)
        os.replace(tmp, p)
        return
    except Exception:  # noqa: BLE001
        pass
    import yaml

    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    os.replace(tmp, p)


def load_playbook() -> str:
    """读已采纳经验，拼成 system prompt 段；无文件/无经验返回空字符串。"""
    if not _playbook_cfg().exists():
        return ""
    try:
        data = _read_yaml(_playbook_cfg())
        lessons = data.get("lessons") or []
        if not lessons:
            return ""
        lines = []
        for l in lessons:
            text = (l.get("text") if isinstance(l, dict) else str(l)).strip()
            if text:
                lines.append(f"- {text}")
        if not lines:
            return ""
        return "\n【经验手册（已采纳，须遵守）】\n" + "\n".join(lines) + "\n"
    except Exception as e:  # noqa: BLE001
        logger.warning("playbook 读取失败（降级跳过）: %s", e)
        return ""


def propose_lesson(text: str) -> bool:
    """把一条经验存入 proposed 草稿（人工采纳后才生效）。返回是否成功。"""
    text = (text or "").strip()
    if not text:
        return False
    try:
        data = _read_yaml(_playbook_cfg()) if _playbook_cfg().exists() else {}
        proposed = data.get("proposed") or []
        if any((p.get("text") if isinstance(p, dict) else p) == text for p in proposed):
            return True  # 已存在草稿
        proposed.append({"text": text})
        data["proposed"] = proposed
        if "lessons" not in data:
            data["lessons"] = []
        _write_yaml_atomic(_playbook_cfg(), data)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("经验草稿写入失败: %s", e)
        return False


def list_proposed() -> list:
    if not _playbook_cfg().exists():
        return []
    try:
        data = _read_yaml(_playbook_cfg())
        return [
            (p.get("text") if isinstance(p, dict) else str(p))
            for p in (data.get("proposed") or [])
        ]
    except Exception:  # noqa: BLE001
        return []


def accept_lesson(text: str) -> bool:
    """采纳草稿经验进 lessons（热加载，下轮对话生效）。返回是否成功。"""
    text = (text or "").strip()
    if not text:
        return False
    try:
        data = _read_yaml(_playbook_cfg()) if _playbook_cfg().exists() else {}
        proposed = data.get("proposed") or []
        lessons = data.get("lessons") or []
        if any((l.get("text") if isinstance(l, dict) else l) == text for l in lessons):
            # 已采纳：仅从 proposed 移除
            data["proposed"] = [
                p for p in proposed
                if (p.get("text") if isinstance(p, dict) else p) != text
            ]
            _write_yaml_atomic(_playbook_cfg(), data)
            return True
        data["proposed"] = [
            p for p in proposed if (p.get("text") if isinstance(p, dict) else p) != text
        ]
        lessons.append({"text": text})
        data["lessons"] = lessons
        _write_yaml_atomic(_playbook_cfg(), data)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("经验采纳失败: %s", e)
        return False


async def chat_learn_write(
    user_msg: str, reply: str, tool_entries: list, extra_search: list | None = None
) -> int:
    """把「任一工具调用成功」的实质性回答沉淀为 chat_fact（M11-1 知识层）。

    失败降级返回 0。门槛：任一 tool ok 即沉淀（web_search / mcp / data_proc 均可），
    使 MCP-only 回答也能被学习（R1 修复）。来源从 tool_entries 派生：
    - web_search：检索记录里的 source/url
    - mcp:*：工具全名（外部知识来源）
    extra_search 仅作兼容并入。

    **占位（is_mock）记录一律不作为来源**（2026-09-19）：此前缺密钥的源静默产出
    `https://mock.local/...` 占位，会被当成来源写进知识库、且带着 `credibility: high`
    —— 假来源被固化进长期记忆，比显示层问题更严重（后续召回会把它当事实依据）。
    """
    ok_entries = [t for t in tool_entries if isinstance(t, dict) and t.get("ok")]
    if not ok_entries:
        return 0
    from .kb_store import kb_write

    sources: list = []
    for t in ok_entries:
        res = t.get("result")
        name = t.get("tool") or "tool"
        if isinstance(res, list):
            for item in res:
                if isinstance(item, dict):
                    if item.get("is_mock"):
                        continue  # 占位不是来源
                    s = item.get("source") or item.get("url") or ""
                    if s and s not in sources:
                        sources.append(s)
        elif name.startswith("mcp:") and res is not None:
            if name not in sources:
                sources.append(name)
    # 兼容旧调用（extra_search 仍可能带来 web_search 来源）；同样剔除占位
    for e in (extra_search or []):
        if isinstance(e, dict):
            if e.get("is_mock"):
                continue
            s = e.get("source") or e.get("url") or ""
            if s and s not in sources:
                sources.append(s)
    entry = {
        "kind": "chat_fact",
        "topic": user_msg[:200],
        "title": user_msg[:60] + ("…" if len(user_msg) > 60 else ""),
        "content": reply + "\n\n工具来源：" + (", ".join(sources[:8]) or "无"),
        "source": "chat_agent",
        "metadata": {
            "credibility": "high",
            "tool_sources": sources[:8],
            "auto": True,
        },
    }
    try:
        return await kb_write([entry])
    except Exception as e:  # noqa: BLE001
        logger.warning("chat 知识沉淀失败（降级跳过）: %s", e)
        return 0
